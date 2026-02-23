#!/usr/bin/env python3
"""XRP/USDT 무기한선물 자동매매 봇 - 메인 엔트리포인트.

MA+RSI+BB+MTF 4지표 과반수 투표 전략.
Bybit V5 API (pybit) 사용.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import logging
from datetime import datetime, timezone

from src.config import Config
from src.exchange import BybitExchange
from src.indicators import calc_all_indicators
from src.strategy import generate_signals
from src.risk_manager import RiskManager
from src.position import PositionManager
from src.logger import BotLogger
from src.telegram_bot import TelegramNotifier
from src.utils import timestamp_now, pct_change, seconds_until_next_hour

logger = logging.getLogger("xrp_bot")


class TradingBot:
    """XRP 자동매매 봇 메인 클래스."""

    def __init__(self):
        self.bot_logger = BotLogger()
        self.notifier = TelegramNotifier()
        self.exchange = BybitExchange()
        self.risk_mgr = RiskManager(self.bot_logger)
        self.pos_mgr = PositionManager(
            self.exchange, self.risk_mgr, self.bot_logger, self.notifier
        )
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        self.last_hourly_run: str = ""
        self.last_daily_summary: str = ""
        self.avg_spread: float = 0.0
        self.spread_samples: list[float] = []

    def run(self):
        """메인 실행 루프."""
        logger.info("=" * 60)
        logger.info("XRP 자동매매 봇 시작")
        logger.info(f"심볼: {Config.SYMBOL}")
        logger.info(f"레버리지: {Config.LEVERAGE}x")
        logger.info(f"테스트넷: {Config.BYBIT_TESTNET}")
        logger.info(f"포지션 사이즈: {Config.POSITION_SIZE_PCT}%")
        logger.info(f"SL: -{Config.STOP_LOSS_PCT}% | TP: +{Config.TAKE_PROFIT_PCT}%")
        logger.info("=" * 60)

        self.notifier.send(
            f"🚀 <b>봇 시작</b>\n"
            f"심볼: {Config.SYMBOL}\n"
            f"레버리지: {Config.LEVERAGE}x\n"
            f"테스트넷: {'Yes' if Config.BYBIT_TESTNET else '⚠️ LIVE'}"
        )

        # 초기 포지션 동기화
        self.pos_mgr.sync_with_exchange()

        while self.running:
            try:
                now = datetime.now(timezone.utc)
                hour_key = now.strftime("%Y-%m-%d-%H")
                day_key = now.strftime("%Y-%m-%d")

                # 매 시간 정각 + 10초: 메인 전략 루프
                if now.minute == 0 and now.second >= 10 and hour_key != self.last_hourly_run:
                    self.last_hourly_run = hour_key
                    self._hourly_cycle()

                # 매일 00:00 UTC: 일일 서머리
                if now.hour == 0 and now.minute == 0 and day_key != self.last_daily_summary:
                    self.last_daily_summary = day_key
                    self._daily_summary()

                # 포지션 보유 중: 10초마다 실시간 모니터링
                if self.pos_mgr.has_position():
                    self._monitor_position()

                time.sleep(10)

            except KeyboardInterrupt:
                self._shutdown("사용자 중단 (Ctrl+C)")
                break
            except Exception as e:
                logger.error(f"MAIN_LOOP_ERROR: {e}", exc_info=True)
                self.notifier.notify_warning(f"메인 루프 에러: {e}")
                time.sleep(30)

    def _hourly_cycle(self):
        """1시간 캔들 완성 시 실행되는 메인 전략 루프."""
        logger.info("=" * 40)
        logger.info("HOURLY_CYCLE 시작")

        try:
            # 1. OHLCV 데이터 조회
            df = self.exchange.get_klines()
            if df.empty:
                logger.error("HOURLY: 캔들 데이터 조회 실패")
                return

            # 2. 지표 계산
            df = calc_all_indicators(df)

            # 3. 시그널 생성
            signals = generate_signals(df)
            combined = signals["combined_signal"]
            confidence = signals["confidence"]

            # 4. 현재 지표값 추출
            row = df.iloc[-1]
            indicators = {
                "ema9": round(row.get("ema9", 0), 6),
                "ema20": round(row.get("ema20", 0), 6),
                "ema50": round(row.get("ema50", 0), 6),
                "ema200": round(row.get("ema200", 0), 6),
                "rsi": round(row.get("rsi", 0), 2),
                "bb_upper": round(row.get("bb_upper", 0), 6),
                "bb_mid": round(row.get("bb_mid", 0), 6),
                "bb_lower": round(row.get("bb_lower", 0), 6),
                "bb_pct": round(row.get("bb_pct", 0), 4),
                "bb_width": round(row.get("bb_width", 0), 4),
                "adx": round(row.get("adx", 0), 2),
                "plus_di": round(row.get("plus_di", 0), 2),
                "minus_di": round(row.get("minus_di", 0), 2),
                "ema20_4h": round(row.get("ema20_4h", 0), 6),
                "ema50_4h": round(row.get("ema50_4h", 0), 6),
                "volume_ratio": round(row.get("volume_ratio", 0), 2),
            }

            # 5. 시그널 로그
            candle = {
                "open": round(row["open"], 6),
                "high": round(row["high"], 6),
                "low": round(row["low"], 6),
                "close": round(row["close"], 6),
                "volume": round(row["volume"], 2),
            }

            # 포지션 정보
            pos_info = self.pos_mgr.get_position_info()
            current_position = None
            if pos_info:
                pnl = pct_change(pos_info["entry_price"], row["close"], pos_info["side"])
                current_position = {
                    "side": pos_info["side"],
                    "size": pos_info["size"],
                    "entry_price": pos_info["entry_price"],
                    "unrealized_pnl": round(pnl * pos_info["entry_price"] * pos_info["size"] / 100, 4),
                    "unrealized_pnl_pct": round(pnl, 2),
                }

            # 필터 체크
            filter_result = self.risk_mgr.check_entry_filters(df, self.pos_mgr.has_position())

            # 액션 결정
            action = "HOLD"
            if self.pos_mgr.has_position():
                exit_reason = self.pos_mgr.check_exit(row["close"], combined, indicators)
                if exit_reason:
                    action = f"CLOSE_{exit_reason}"
            elif combined != 0 and filter_result["passed"]:
                can_trade, reason = self.risk_mgr.can_trade()
                if can_trade:
                    action = "OPEN_LONG" if combined == 1 else "OPEN_SHORT"
                else:
                    action = f"BLOCKED_{reason}"

            signal_log = {
                "timestamp": timestamp_now(),
                "candle": candle,
                "indicators": indicators,
                "signals": {
                    k: signals[k] for k in ("MA", "RSI", "BB", "MTF")
                },
                "combined_signal": combined,
                "signal_detail": signals["signal_detail"],
                "filter_check": filter_result,
                "action": action,
                "current_position": current_position,
            }
            self.bot_logger.log_signal(signal_log)

            logger.info(f"SIGNAL: {signals['signal_detail']} → ACTION: {action}")

            # 6. 포지션 보유 중 → 청산 조건 체크
            if self.pos_mgr.has_position():
                exit_reason = self.pos_mgr.check_exit(row["close"], combined, indicators)
                if exit_reason:
                    self.pos_mgr.close_position(row["close"], exit_reason, indicators)

            # 7. 포지션 없음 → 시그널에 따라 진입
            elif combined != 0 and filter_result["passed"]:
                can_trade, reason = self.risk_mgr.can_trade()
                if can_trade:
                    balance = self.exchange.get_balance()
                    equity = balance.get("totalEquity", 0)
                    if equity > 0:
                        margin = self.risk_mgr.calc_position_size(equity, confidence)
                        side = "Buy" if combined == 1 else "Sell"
                        self.pos_mgr.open_position(
                            side=side,
                            margin_usdt=margin,
                            current_price=row["close"],
                            signals=signals,
                            indicators=indicators,
                        )
                    else:
                        logger.error("HOURLY: 잔고 0, 진입 불가")
                else:
                    logger.info(f"HOURLY: 매매 차단 - {reason}")

            # 8. 잔고 로그
            self._log_equity()

        except Exception as e:
            logger.error(f"HOURLY_CYCLE_ERROR: {e}", exc_info=True)
            self.notifier.notify_warning(f"시간별 사이클 에러: {e}")

    def _monitor_position(self):
        """포지션 실시간 모니터링 (10초 간격)."""
        try:
            ticker = self.exchange.get_ticker()
            current_price = ticker.get("last_price", 0)
            if current_price <= 0:
                return

            # 스프레드 추적
            spread = ticker.get("ask1", 0) - ticker.get("bid1", 0)
            if spread > 0:
                self.spread_samples.append(spread)
                if len(self.spread_samples) > 100:
                    self.spread_samples = self.spread_samples[-100:]
                self.avg_spread = sum(self.spread_samples) / len(self.spread_samples)

            exit_reason = self.pos_mgr.check_exit(current_price, 0, {})
            if exit_reason:
                logger.info(f"MONITOR: 청산 트리거 - {exit_reason}")
                # 현재 지표값 간이 조회 (실시간이므로 간략히)
                self.pos_mgr.close_position(current_price, exit_reason, {})

        except Exception as e:
            logger.error(f"MONITOR_ERROR: {e}")

    def _log_equity(self):
        """잔고 데이터 로그."""
        try:
            balance = self.exchange.get_balance()
            pos = self.exchange.get_position()
            unrealized_pnl = pos.get("unrealized_pnl", 0) if pos else 0

            today_trades = self.bot_logger.get_today_trades()
            realized_today = sum(t.get("net_pnl_usdt", 0) for t in today_trades)

            all_trades = self.bot_logger.get_recent_trades(limit=200)
            cumulative = sum(t.get("net_pnl_usdt", 0) for t in all_trades)

            equity = balance.get("totalEquity", 0)
            # 간단한 peak 추적 (추후 개선 가능)
            drawdown = 0.0

            # 7일 승률 계산
            from datetime import timedelta
            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent = [t for t in all_trades if t.get("timestamp_close", "") >= seven_days_ago]
            wins_7d = sum(1 for t in recent if t.get("net_pnl_pct", 0) > 0)
            win_rate_7d = (wins_7d / len(recent) * 100) if recent else 0

            self.bot_logger.log_equity({
                "timestamp": timestamp_now(),
                "total_equity": round(equity, 2),
                "available_balance": round(balance.get("availableBalance", 0), 2),
                "position_margin": round(equity - balance.get("availableBalance", 0), 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "realized_pnl_today": round(realized_today, 2),
                "cumulative_pnl": round(cumulative, 2),
                "drawdown_from_peak": round(drawdown, 2),
                "num_trades_today": len(today_trades),
                "win_rate_7d": round(win_rate_7d, 1),
            })
        except Exception as e:
            logger.error(f"EQUITY_LOG_ERROR: {e}")

    def _daily_summary(self):
        """일일 서머리 생성 및 텔레그램 발송."""
        try:
            balance = self.exchange.get_balance()
            equity = balance.get("totalEquity", 0)

            today_trades = self.bot_logger.get_today_trades()
            realized_today = sum(t.get("net_pnl_usdt", 0) for t in today_trades)

            pos = self.exchange.get_position()
            unrealized = pos.get("unrealized_pnl", 0) if pos else 0

            # 전일 대비 변화율 (간이)
            all_trades = self.bot_logger.get_recent_trades(limit=200)
            cumulative = sum(t.get("net_pnl_usdt", 0) for t in all_trades)
            initial_equity = equity - cumulative if cumulative else equity
            equity_change_pct = ((equity - initial_equity) / initial_equity * 100) if initial_equity > 0 else 0

            # 현재 포지션
            current_position = None
            if pos:
                current_position = {
                    "side": pos.get("side"),
                    "size": pos.get("size", 0),
                    "entry_price": pos.get("entry_price", 0),
                    "unrealized_pnl_pct": pct_change(pos.get("entry_price", 0),
                                                      self.exchange.get_ticker().get("last_price", 0),
                                                      pos.get("side", "Buy")),
                    "unrealized_pnl": unrealized,
                }

            # 7일 통계
            from datetime import timedelta
            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent = [t for t in all_trades if t.get("timestamp_close", "") >= seven_days_ago]
            wins = [t for t in recent if t.get("net_pnl_pct", 0) > 0]
            losses = [t for t in recent if t.get("net_pnl_pct", 0) <= 0]
            avg_win = sum(t.get("net_pnl_pct", 0) for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.get("net_pnl_pct", 0) for t in losses) / len(losses) if losses else 0
            total_wins = sum(t.get("net_pnl_usdt", 0) for t in wins)
            total_losses = abs(sum(t.get("net_pnl_usdt", 0) for t in losses))
            pf = total_wins / total_losses if total_losses > 0 else 0

            stats_7d = {
                "win_rate": (len(wins) / len(recent) * 100) if recent else 0,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": pf,
                "max_drawdown": min((t.get("net_pnl_pct", 0) for t in recent), default=0),
            }

            summary = self.notifier.format_daily_summary(
                total_equity=equity,
                equity_change_pct=equity_change_pct,
                realized_pnl=realized_today,
                unrealized_pnl=unrealized,
                trades_today=today_trades,
                current_position=current_position,
                stats_7d=stats_7d,
            )
            self.notifier.notify_daily_summary(summary)
            logger.info("DAILY_SUMMARY: 발송 완료")

        except Exception as e:
            logger.error(f"DAILY_SUMMARY_ERROR: {e}", exc_info=True)

    def _shutdown(self, reason: str):
        """봇 종료."""
        self.running = False
        logger.info(f"BOT_SHUTDOWN: {reason}")
        self.notifier.notify_critical(f"봇 종료: {reason}")


def main():
    parser = argparse.ArgumentParser(description="XRP 자동매매 봇")
    parser.add_argument("--testnet", action="store_true", help="테스트넷 모드")
    args = parser.parse_args()

    if args.testnet:
        Config.set_testnet(True)
        print("[INFO] 테스트넷 모드로 실행")
    elif not Config.BYBIT_TESTNET:
        print("[WARNING] ⚠️  실전(LIVE) 모드로 실행합니다!")

    errors = Config.validate()
    if errors:
        for e in errors:
            print(f"[ERROR] {e}")
        sys.exit(1)

    bot = TradingBot()

    # 시그널 핸들러
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        bot._shutdown(f"Signal {sig_name}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    bot.run()


if __name__ == "__main__":
    main()
