#!/usr/bin/env python3
"""XRP/USDT 외 멀티코인 무기한선물 자동매매 봇.

MA+RSI+BB+MTF 4지표 과반수 투표 전략.
Bybit V5 API (pybit) 사용.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import logging
from datetime import datetime, timezone, timedelta

from src.config import Config
from src.exchange import BybitExchange
from src.indicators import calc_all_indicators
from src.strategy import generate_signals
from src.risk_manager import RiskManager
from src.position import PositionManager
from src.logger import BotLogger
from src.telegram_bot import TelegramNotifier
from src.utils import timestamp_now, pct_change

logger = logging.getLogger("xrp_bot")


class TradingBot:
    """멀티코인 자동매매 봇 메인 클래스."""

    def __init__(self):
        self.bot_logger = BotLogger()
        self.notifier = TelegramNotifier()
        self.exchange = BybitExchange()
        self.risk_mgr = RiskManager(self.bot_logger)

        # 멀티심볼: 심볼별 PositionManager
        self.symbols = Config.SYMBOLS
        self.pos_managers: dict[str, PositionManager] = {}
        for sym in self.symbols:
            self.pos_managers[sym] = PositionManager(
                self.exchange, self.risk_mgr, self.bot_logger, self.notifier,
                symbol=sym,
            )

        self.running = True
        self.paused = False
        self.start_time = datetime.now(timezone.utc)
        self.last_signal_run: str = ""
        self.last_daily_summary: str = ""
        self.avg_spread: float = 0.0
        self.spread_samples: list[float] = []

        # 심볼별 시그널/지표 캐시
        self.last_signals: dict[str, dict] = {}
        self.last_indicators: dict[str, dict] = {}

        # 캔들 마감 기준 진입용: 심볼별 마지막 처리 캔들 timestamp
        self.last_processed_candle_ts: dict[str, str] = {}

        # 일일 전략 리뷰
        self.last_strategy_review: str = ""
        self.pending_suggestions: list[dict] = []

        # 텔레그램 명령어 핸들러 등록
        self.notifier.set_command_handler(self._handle_command)

    def run(self):
        """메인 실행 루프."""
        logger.info("=" * 60)
        logger.info("멀티코인 자동매매 봇 시작")
        sym_names = ", ".join(s.replace("USDT", "") for s in self.symbols)
        logger.info(f"심볼: {sym_names}")
        logger.info(f"레버리지: {Config.LEVERAGE}x")
        logger.info(f"테스트넷: {Config.BYBIT_TESTNET}")
        logger.info(f"SL: -{Config.STOP_LOSS_PCT}% | TP: +{Config.TAKE_PROFIT_PCT}%")
        logger.info("=" * 60)

        mode = "Yes" if Config.BYBIT_TESTNET else "\u26a0\ufe0f LIVE"
        self.notifier.send(
            f"\U0001f680 <b>봇 시작</b>\n"
            f"코인: {sym_names}\n"
            f"레버리지: {Config.LEVERAGE}x\n"
            f"테스트넷: {mode}\n"
            f"SL: -{Config.STOP_LOSS_PCT}% | TP: +{Config.TAKE_PROFIT_PCT}%\n"
            f"시그널: 10분 간격 | 모니터링: 10초\n"
            f"/도움 으로 명령어 확인"
        )

        # 초기 포지션 동기화
        for sym, mgr in self.pos_managers.items():
            mgr.sync_with_exchange()

        while self.running:
            try:
                now = datetime.now(timezone.utc)
                signal_key = now.strftime("%Y-%m-%d-%H") + f"-{(now.minute // 10) * 10:02d}"
                day_key = now.strftime("%Y-%m-%d")

                # 텔레그램 명령어 폴링
                self.notifier.poll_commands()

                # 10분마다 시그널 분석 (모든 심볼)
                if now.minute % 10 == 0 and now.second >= 10 and signal_key != self.last_signal_run:
                    self.last_signal_run = signal_key
                    self._signal_cycle()

                # 매일 00:00 UTC: 일일 서머리
                if now.hour == 0 and now.minute == 0 and day_key != self.last_daily_summary:
                    self.last_daily_summary = day_key
                    self._daily_summary()

                # 매일 한국시간 09:00 (UTC 00:00): 전략 리뷰 + 차트 분석
                if now.hour == 0 and now.minute == 5 and day_key != self.last_strategy_review:
                    self.last_strategy_review = day_key
                    self._daily_chart_analysis()
                    self._daily_strategy_review()

                # 포지션 모니터링 (10초마다)
                has_any_position = False
                for sym, mgr in self.pos_managers.items():
                    if mgr.has_position():
                        has_any_position = True
                        self._monitor_position(sym, mgr)
                    else:
                        mgr.sync_with_exchange()

                time.sleep(10)

            except KeyboardInterrupt:
                self._shutdown("사용자 중단 (Ctrl+C)")
                break
            except Exception as e:
                logger.error(f"MAIN_LOOP_ERROR: {e}", exc_info=True)
                self.notifier.notify_warning(f"메인 루프 에러: {e}")
                time.sleep(30)

    # ──────────────────────────────────────────────
    # 텔레그램 명령어 핸들러
    # ──────────────────────────────────────────────

    def _handle_command(self, command: str, args: str) -> str:
        handlers = {
            "/help": self._cmd_help, "/도움": self._cmd_help,
            "/status": self._cmd_status, "/현황": self._cmd_status, "/상태": self._cmd_status,
            "/balance": self._cmd_balance, "/잔고": self._cmd_balance,
            "/position": self._cmd_position, "/포지션": self._cmd_position,
            "/signal": self._cmd_signal, "/시그널": self._cmd_signal, "/분석": self._cmd_signal,
            "/close": self._cmd_close, "/청산": self._cmd_close,
            "/long": self._cmd_long, "/롱": self._cmd_long, "/매수": self._cmd_long,
            "/short": self._cmd_short, "/숏": self._cmd_short, "/매도": self._cmd_short,
            "/pause": self._cmd_pause, "/중지": self._cmd_pause,
            "/resume": self._cmd_resume, "/재개": self._cmd_resume, "/시작": self._cmd_resume,
            "/trades": self._cmd_trades, "/매매내역": self._cmd_trades,
            "/journal": self._cmd_journal, "/일지": self._cmd_journal, "/매매일지": self._cmd_journal,
            "/pnl": self._cmd_pnl, "/손익": self._cmd_pnl,
            "/config": self._cmd_config, "/설정": self._cmd_config,
            "/set": self._cmd_set, "/변경": self._cmd_set,
            "/review": self._cmd_review, "/리뷰": self._cmd_review, "/전략": self._cmd_review,
            "/approve": self._cmd_approve, "/승인": self._cmd_approve,
        }
        handler = handlers.get(command)
        if handler:
            return handler(args)
        return f"알 수 없는 명령어: {command}\n/도움 으로 명령어 확인"

    def _cmd_help(self, args: str) -> str:
        sym_names = ", ".join(s.replace("USDT", "") for s in self.symbols)
        return (
            "\U0001f4cb <b>명령어 목록</b>\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f4b0 코인: {sym_names}\n\n"
            "\U0001f4ca <b>조회</b>\n"
            "/현황 - 전체 현황\n"
            "/잔고 - 잔고 조회\n"
            "/포지션 - 모든 포지션 상세\n"
            "/분석 - 모든 코인 시그널\n"
            "/분석 BTC - 특정 코인 시그널\n"
            "/매매내역 - 최근 매매\n"
            "/매매일지 - 사람이 읽기 쉬운 매매 일지 요약\n"
            "/손익 - 손익 요약\n"
            "/설정 - 현재 설정\n\n"
            "\U0001f3af <b>매매</b>\n"
            "/롱 BTC - 수동 롱 (코인 지정)\n"
            "/숏 ETH - 수동 숏 (코인 지정)\n"
            "/청산 - 모든 포지션 청산\n"
            "/청산 SOL - 특정 코인만 청산\n\n"
            "\u2699\ufe0f <b>제어</b>\n"
            "/중지 - 자동매매 일시중지\n"
            "/재개 - 자동매매 재개\n"
            "/변경 레버리지 3\n"
            "/변경 손절 2.5\n"
            "/변경 익절 5.0\n\n"
            "\U0001f9e0 <b>전략 리뷰</b>\n"
            "/리뷰 - 전략 분석 즉시 실행\n"
            "/승인 1 - 추천 #1 적용\n"
            "/승인 전체 - 모든 추천 적용"
        )

    def _cmd_status(self, args: str) -> str:
        balance = self.exchange.get_balance()
        equity = balance.get("totalEquity", 0)
        avail = balance.get("availableBalance", 0)

        # 포지션 목록
        pos_lines = []
        for sym, mgr in self.pos_managers.items():
            if mgr.has_position():
                name = sym.replace("USDT", "")
                ticker = self.exchange.get_ticker(symbol=sym)
                last = ticker.get("last_price", 0)
                pnl = pct_change(mgr.entry_price, last, mgr.side)
                # Estimated USD PnL using internal qty (real exchange position may differ if out-of-sync)
                pos_value = mgr.entry_price * mgr.qty
                pnl_usdt = pos_value * (pnl / 100)
                direction = "L" if mgr.side == "Buy" else "S"
                pos_lines.append(f"  {name} {direction} {pnl:+.2f}% (${pnl_usdt:+.2f})")

        pos_str = "\n".join(pos_lines) if pos_lines else "  없음"

        uptime = datetime.now(timezone.utc) - self.start_time
        hours = uptime.total_seconds() / 3600
        pause_str = "\u23f8 일시중지" if self.paused else "\u25b6 운영중"
        risk = self.risk_mgr.get_status()

        return (
            f"\U0001f4ca <b>봇 현황</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f4b0 잔고: ${equity:.2f} (가용: ${avail:.2f})\n"
            f"\U0001f4c8 포지션:\n{pos_str}\n"
            f"\u23f0 가동: {hours:.1f}시간\n"
            f"\U0001f3ae 상태: {pause_str}\n"
            f"\U0001f4c5 오늘 매매: {risk['daily_trade_count']}회\n"
            f"\U0001f4c9 오늘 PnL: {risk['daily_pnl']:+.2f}%"
        )

    def _cmd_balance(self, args: str) -> str:
        balance = self.exchange.get_balance()
        return (
            f"\U0001f4b0 <b>잔고</b>\n"
            f"총 자산: ${balance.get('totalEquity', 0):.2f}\n"
            f"가용 잔고: ${balance.get('availableBalance', 0):.2f}\n"
            f"마진 잔고: ${balance.get('totalMarginBalance', 0):.2f}\n"
            f"지갑 잔고: ${balance.get('totalWalletBalance', 0):.2f}"
        )

    def _cmd_position(self, args: str) -> str:
        lines = ["\U0001f4c8 <b>포지션 현황</b>"]
        has_any = False

        for sym, mgr in self.pos_managers.items():
            pos = self.exchange.get_position(symbol=sym)
            if not pos:
                continue
            has_any = True
            name = sym.replace("USDT", "")
            ticker = self.exchange.get_ticker(symbol=sym)
            current = ticker.get("last_price", 0)
            pnl = pct_change(pos["entry_price"], current, pos["side"])
            direction = "Long" if pos["side"] == "Buy" else "Short"
            # Exchange-reported PnL amount
            upnl = float(pos.get("unrealized_pnl", 0) or 0)

            sl = mgr._calc_sl_price() if mgr.side else 0
            tp = mgr._calc_tp_price() if mgr.side else 0

            lines.append(
                f"\n<b>{name}</b> {direction}\n"
                f"  수량: {pos['size']} | 레버: {pos['leverage']}x\n"
                f"  진입: ${pos['entry_price']:.4f} | 현재: ${current:.4f}\n"
                f"  PnL: {pnl:+.2f}% (${upnl:+.2f})\n"
                f"  SL: ${sl:.4f} | TP: ${tp:.4f}"
            )

        if not has_any:
            lines.append("\n\U0001f4ad 포지션 없음")
        return "\n".join(lines)

    def _cmd_signal(self, args: str) -> str:
        # 특정 코인 지정
        target = args.strip().upper()
        if target:
            target_sym = target + "USDT" if not target.endswith("USDT") else target
            sig = self.last_signals.get(target_sym)
            if not sig:
                return f"\U0001f4e1 {target} 시그널 없음"
            return self._format_signal(target_sym, sig)

        # 전체 시그널 요약
        if not self.last_signals:
            return "\U0001f4e1 아직 시그널 분석 없음 (10분 간격 분석)"

        lines = ["\U0001f4e1 <b>시그널 요약</b>"]
        icons = {1: "\u2705", -1: "\u274c", 0: "\u2b1c"}
        for sym in self.symbols:
            sig = self.last_signals.get(sym, {})
            name = sym.replace("USDT", "")
            combined = sig.get("combined_signal", 0)
            confidence = sig.get("confidence", 0)
            icon = icons.get(combined, "\u2b1c")
            detail = sig.get("signal_detail", "N/A")
            lines.append(f"{icon} <b>{name}</b>: {detail}")

        return "\n".join(lines)

    def _format_signal(self, symbol: str, sig: dict) -> str:
        name = symbol.replace("USDT", "")
        icons = {1: "\u2705", -1: "\u274c", 0: "\u2b1c"}
        default_icon = "\u2b1c"
        lines = []
        for k in ("MA", "RSI", "BB", "MTF"):
            s = sig.get(k, {})
            val = s.get("value", 0) if isinstance(s, dict) else 0
            reason = s.get("reason", "N/A") if isinstance(s, dict) else "N/A"
            icon = icons.get(val, default_icon)
            lines.append(f"  {icon} {k}: {reason}")

        return (
            f"\U0001f4e1 <b>{name} 시그널</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            + "\n".join(lines) + "\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"결과: {sig.get('signal_detail', 'N/A')}"
        )

    def _cmd_close(self, args: str) -> str:
        target = args.strip().upper()

        if target:
            # 특정 코인 청산
            target_sym = target + "USDT" if not target.endswith("USDT") else target
            mgr = self.pos_managers.get(target_sym)
            if not mgr or not mgr.has_position():
                return f"\u274c {target} 포지션 없음"
            ticker = self.exchange.get_ticker(symbol=target_sym)
            current = ticker.get("last_price", 0)
            result = mgr.close_position(current, "MANUAL_CLOSE", {})
            if result:
                return f"\u2705 {target} 청산 완료 | PnL: {result['pnl_pct']:+.2f}%"
            return f"\u274c {target} 청산 실패"

        # 전체 청산
        closed = []
        for sym, mgr in self.pos_managers.items():
            if mgr.has_position():
                name = sym.replace("USDT", "")
                ticker = self.exchange.get_ticker(symbol=sym)
                current = ticker.get("last_price", 0)
                result = mgr.close_position(current, "MANUAL_CLOSE", {})
                if result:
                    closed.append(f"{name} {result['pnl_pct']:+.2f}%")

        if closed:
            return "\u2705 청산 완료:\n" + "\n".join(f"  {c}" for c in closed)
        return "\u274c 청산할 포지션이 없습니다"

    def _resolve_symbol(self, args: str) -> str | None:
        """args에서 심볼 추출. 없으면 None."""
        target = args.strip().upper()
        if not target:
            return None
        target_sym = target + "USDT" if not target.endswith("USDT") else target
        if target_sym in self.pos_managers:
            return target_sym
        return None

    def _cmd_long(self, args: str) -> str:
        sym = self._resolve_symbol(args)
        if not sym:
            return "\u274c 코인을 지정해주세요. 예: /롱 BTC"

        mgr = self.pos_managers[sym]
        if mgr.has_position():
            return f"\u274c {sym.replace('USDT', '')} 이미 포지션 보유 중"

        balance = self.exchange.get_balance()
        equity = balance.get("totalEquity", 0)
        avail = balance.get("availableBalance", 0)
        if equity <= 0:
            return "\u274c 잔고 부족"

        ticker = self.exchange.get_ticker(symbol=sym)
        price = ticker.get("last_price", 0)
        if price <= 0:
            return "\u274c 가격 조회 실패"

        qty, detail = self.risk_mgr.calc_qty_from_equity(
            equity=equity,
            confidence=2,
            mark_price=price,
            qty_step=mgr.qty_step,
            min_qty=mgr.min_qty,
            available_balance=avail,
        )
        if qty <= 0:
            return f"\u274c 수량 계산 불가: {detail.get('reason', 'unknown')}"

        ok = mgr.open_position(
            side="Buy", margin_usdt=0, current_price=price,
            signals={"confidence": 2}, indicators={},
            qty_override=qty,
        )
        name = sym.replace("USDT", "")
        return f"\u2705 {name} 롱 진입 @ ${price:.4f}" if ok else f"\u274c {name} 롱 진입 실패"

    def _cmd_short(self, args: str) -> str:
        sym = self._resolve_symbol(args)
        if not sym:
            return "\u274c 코인을 지정해주세요. 예: /숏 ETH"

        mgr = self.pos_managers[sym]
        if mgr.has_position():
            return f"\u274c {sym.replace('USDT', '')} 이미 포지션 보유 중"

        balance = self.exchange.get_balance()
        equity = balance.get("totalEquity", 0)
        avail = balance.get("availableBalance", 0)
        if equity <= 0:
            return "\u274c 잔고 부족"

        ticker = self.exchange.get_ticker(symbol=sym)
        price = ticker.get("last_price", 0)
        if price <= 0:
            return "\u274c 가격 조회 실패"

        qty, detail = self.risk_mgr.calc_qty_from_equity(
            equity=equity,
            confidence=2,
            mark_price=price,
            qty_step=mgr.qty_step,
            min_qty=mgr.min_qty,
            available_balance=avail,
        )
        if qty <= 0:
            return f"\u274c 수량 계산 불가: {detail.get('reason', 'unknown')}"

        ok = mgr.open_position(
            side="Sell", margin_usdt=0, current_price=price,
            signals={"confidence": 2}, indicators={},
            qty_override=qty,
        )
        name = sym.replace("USDT", "")
        return f"\u2705 {name} 숏 진입 @ ${price:.4f}" if ok else f"\u274c {name} 숏 진입 실패"

    def _cmd_pause(self, args: str) -> str:
        self.paused = True
        logger.info("BOT: 자동매매 일시중지 (텔레그램)")
        return "\u23f8 자동매매 일시중지됨\n/재개 로 다시 시작"

    def _cmd_resume(self, args: str) -> str:
        self.paused = False
        logger.info("BOT: 자동매매 재개 (텔레그램)")
        return "\u25b6 자동매매 재개됨"

    def _cmd_trades(self, args: str) -> str:
        trades = self.bot_logger.get_recent_trades(limit=5)
        if not trades:
            return "\U0001f4ad 매매 내역 없음"

        lines = ["\U0001f4cb <b>최근 매매</b>"]
        for t in reversed(trades):
            pnl = t.get("net_pnl_pct", 0)
            icon = "\u2705" if pnl > 0 else "\u274c"
            sym_name = t.get("symbol", "").replace("USDT", "")
            direction = t.get("direction", "")
            reason = t.get("exit_reason", "")
            lines.append(
                f"{icon} {sym_name} {direction} {pnl:+.1f}% | ${t.get('net_pnl_usdt', 0):+.2f} | {reason}"
            )
        return "\n".join(lines)

    def _cmd_journal(self, args: str) -> str:
        """매매 일지를 사람이 읽기 쉬운 한글로 요약."""
        trades = self.bot_logger.get_recent_trades(limit=200)
        today_trades = self.bot_logger.get_today_trades()

        balance = self.exchange.get_balance()
        equity = balance.get("totalEquity", 0)
        avail = balance.get("availableBalance", 0)

        # 누적 손익
        total_pnl = sum(t.get("net_pnl_usdt", 0) for t in trades)
        today_pnl = sum(t.get("net_pnl_usdt", 0) for t in today_trades)

        # 승/패
        total_wins = sum(1 for t in trades if t.get("net_pnl_pct", 0) > 0)
        total_losses = len(trades) - total_wins
        today_wins = sum(1 for t in today_trades if t.get("net_pnl_pct", 0) > 0)
        today_losses = len(today_trades) - today_wins

        # 최근 5개를 '설명형'으로
        reason_map = {
            "TP_HIT": "익절 도달",
            "SL_HIT": "손절 도달",
            "TRAILING_STOP": "트레일링 스탑",
            "TIME_EXIT": "시간 청산",
            "MANUAL_CLOSE": "수동 청산",
            "SERVER_TP": "서버 TP",
            "SERVER_SL": "서버 SL",
            "SERVER_CLOSE": "서버 청산",
        }
        def explain_trade(t: dict) -> str:
            sym = (t.get("symbol", "").replace("USDT", "") or "-")
            direction = t.get("direction", "")
            pnl_pct = t.get("net_pnl_pct", 0)
            pnl_usdt = t.get("net_pnl_usdt", 0)
            reason = reason_map.get(t.get("exit_reason", ""), t.get("exit_reason", ""))
            entry = t.get("entry_price", 0)
            exitp = t.get("exit_price", 0)
            holding = t.get("holding_hours", 0)
            sign = "+" if pnl_usdt >= 0 else ""
            return (
                f"- {sym} {direction}: {sign}{pnl_pct:.2f}% ({sign}${pnl_usdt:.2f})\n"
                f"  · 진입가→청산가: ${entry:.4f} → ${exitp:.4f}\n"
                f"  · 종료 사유: {reason}\n"
                f"  · 보유 시간: {holding:.1f}h"
            )

        recent5 = trades[-5:]
        recent_lines = "\n".join(explain_trade(t) for t in reversed(recent5)) if recent5 else "- 없음"

        # 현재 포지션 요약
        pos_lines = []
        for sym, mgr in self.pos_managers.items():
            if mgr.has_position():
                name = sym.replace("USDT", "")
                ticker = self.exchange.get_ticker(symbol=sym)
                current = ticker.get("last_price", 0)
                pnl = pct_change(mgr.entry_price, current, mgr.side)
                direction = "롱" if mgr.side == "Buy" else "숏"
                pos_lines.append(f"- {name} {direction}: {pnl:+.2f}% (진입 ${mgr.entry_price:.4f} / 현재 ${current:.4f})")
        pos_str = "\n".join(pos_lines) if pos_lines else "- 없음"

        # 앞으로의 상황(규칙 기반, 예측 아님)
        risk = self.risk_mgr.get_status()
        can_trade = "가능" if risk.get("can_trade") else "차단"
        next_note = (
            f"- 자동매매 상태: {'중지' if self.paused else '운영중'}\n"
            f"- 오늘 매매 횟수: {risk.get('daily_trade_count')}회 (제한 없음)\n"
            f"- 쿨다운/차단 여부: {can_trade}\n"
            f"- 손절 연속: {risk.get('consecutive_sl')}회 (기준 {Config.COOLDOWN_AFTER_SL_STREAK})"
        )

        return (
            f"\U0001f4d3 <b>매매 일지(요약)</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f4b0 현재 자산: ${equity:.2f} (가용 ${avail:.2f})\n"
            f"\U0001f4c8 현재 포지션:\n{pos_str}\n\n"
            f"\U0001f4c5 오늘 요약: {len(today_trades)}회 / {today_wins}승 {today_losses}패 | 손익 {today_pnl:+.2f}$\n"
            f"\U0001f4ca 누적 요약: {len(trades)}회 / {total_wins}승 {total_losses}패 | 누적손익 {total_pnl:+.2f}$\n\n"
            f"\U0001f9fe 최근 매매 5건(설명형):\n{recent_lines}\n\n"
            f"\U0001f527 앞으로의 상태(규칙 기반):\n{next_note}"
        )

    def _cmd_pnl(self, args: str) -> str:
        today_trades = self.bot_logger.get_today_trades()
        all_trades = self.bot_logger.get_recent_trades(limit=200)

        today_pnl = sum(t.get("net_pnl_usdt", 0) for t in today_trades)
        total_pnl = sum(t.get("net_pnl_usdt", 0) for t in all_trades)
        today_wins = sum(1 for t in today_trades if t.get("net_pnl_pct", 0) > 0)
        total_wins = sum(1 for t in all_trades if t.get("net_pnl_pct", 0) > 0)

        return (
            f"\U0001f4b5 <b>손익 요약</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"오늘: ${today_pnl:+.2f} ({len(today_trades)}매매, {today_wins}승)\n"
            f"전체: ${total_pnl:+.2f} ({len(all_trades)}매매, {total_wins}승)\n"
            f"승률: {(total_wins/len(all_trades)*100) if all_trades else 0:.0f}%"
        )

    def _cmd_config(self, args: str) -> str:
        sym_names = ", ".join(s.replace("USDT", "") for s in self.symbols)
        return (
            f"\u2699\ufe0f <b>현재 설정</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"코인: {sym_names}\n"
            f"레버리지: {Config.LEVERAGE}x\n"
            f"포지션 사이즈: {Config.POSITION_SIZE_PCT}%\n"
            f"SL: -{Config.STOP_LOSS_PCT}%\n"
            f"TP: +{Config.TAKE_PROFIT_PCT}%\n"
            f"트레일링 활성: +{Config.TRAILING_STOP_ACTIVATE_PCT}%\n"
            f"트레일링 콜백: -{Config.TRAILING_STOP_CALLBACK_PCT}%\n"
            f"테스트넷: {Config.BYBIT_TESTNET}\n"
            f"자동매매: {'중지' if self.paused else '운영중'}"
        )

    def _cmd_set(self, args: str) -> str:
        parts = args.strip().split()
        if len(parts) != 2:
            return (
                "\u2699\ufe0f 사용법: /변경 <항목> <값>\n"
                "예: /변경 레버리지 3\n"
                "    /변경 손절 1.5\n"
                "    /변경 익절 5.0\n"
                "    /변경 사이즈 8"
            )

        key, val_str = parts[0].lower(), parts[1]
        key_map = {
            "레버리지": "leverage", "레버": "leverage",
            "손절": "sl", "sl": "sl",
            "익절": "tp", "tp": "tp",
            "사이즈": "size", "size": "size",
            "트레일링": "trailing", "trailing": "trailing",
            "콜백": "callback", "callback": "callback",
        }
        key = key_map.get(key, key)

        try:
            val = float(val_str)
        except ValueError:
            return f"\u274c 숫자가 아닙니다: {val_str}"

        if key == "leverage":
            val = int(val)
            if val < 1 or val > Config.MAX_LEVERAGE:
                return f"\u274c 레버리지 범위: 1~{Config.MAX_LEVERAGE}"
            old = Config.LEVERAGE
            Config.LEVERAGE = val
            for sym in self.symbols:
                self.exchange.setup_leverage(sym, val)
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "LEVERAGE",
                "old": old,
                "new": val,
            })
            return f"\u2705 레버리지 변경: {val}x (전체 코인)"
        elif key == "sl":
            if val <= 0 or val > 20:
                return "\u274c SL 범위: 0.1~20%"
            old = Config.STOP_LOSS_PCT
            Config.STOP_LOSS_PCT = val
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "STOP_LOSS_PCT",
                "old": old,
                "new": val,
            })
            return f"\u2705 SL 변경: -{val}%"
        elif key == "tp":
            if val <= 0 or val > 50:
                return "\u274c TP 범위: 0.1~50%"
            old = Config.TAKE_PROFIT_PCT
            Config.TAKE_PROFIT_PCT = val
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "TAKE_PROFIT_PCT",
                "old": old,
                "new": val,
            })
            return f"\u2705 TP 변경: +{val}%"
        elif key == "size":
            if val <= 0 or val > Config.MAX_POSITION_SIZE_PCT:
                return f"\u274c 사이즈 범위: 0.1~{Config.MAX_POSITION_SIZE_PCT}%"
            old = Config.POSITION_SIZE_PCT
            Config.POSITION_SIZE_PCT = val
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "POSITION_SIZE_PCT",
                "old": old,
                "new": val,
            })
            return f"\u2705 포지션 사이즈 변경: {val}%"
        elif key == "trailing":
            old = Config.TRAILING_STOP_ACTIVATE_PCT
            Config.TRAILING_STOP_ACTIVATE_PCT = val
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "TRAILING_STOP_ACTIVATE_PCT",
                "old": old,
                "new": val,
            })
            return f"\u2705 트레일링 활성 변경: +{val}%"
        elif key == "callback":
            old = Config.TRAILING_STOP_CALLBACK_PCT
            Config.TRAILING_STOP_CALLBACK_PCT = val
            self.bot_logger.log_config_change({
                "timestamp": timestamp_now(),
                "source": "telegram",
                "key": "TRAILING_STOP_CALLBACK_PCT",
                "old": old,
                "new": val,
            })
            return f"\u2705 트레일링 콜백 변경: -{val}%"
        else:
            return f"\u274c 알 수 없는 설정: {key}"

    # ──────────────────────────────────────────────
    # 전략 루프 (멀티심볼)
    # ──────────────────────────────────────────────

    def _signal_cycle(self):
        """10분마다 실행 - 모든 심볼 순회."""
        logger.info("=" * 40)
        logger.info(f"SIGNAL_CYCLE 시작 ({len(self.symbols)}개 코인)")

        for sym in self.symbols:
            try:
                self._analyze_symbol(sym)
            except Exception as e:
                logger.error(f"SIGNAL_ERROR [{sym}]: {e}", exc_info=True)

        # 잔고 로그 (한 번만)
        self._log_equity()

    def _analyze_symbol(self, symbol: str):
        """개별 심볼 시그널 분석 + 매매 판단."""
        mgr = self.pos_managers[symbol]
        name = symbol.replace("USDT", "")

        # 1. OHLCV 데이터 조회
        df = self.exchange.get_klines(symbol=symbol)
        if df.empty:
            logger.error(f"SIGNAL [{symbol}]: 캔들 데이터 조회 실패")
            return

        # 2. 지표 계산
        df = calc_all_indicators(df)

        # 3. 시그널 생성
        signals = generate_signals(df)
        combined = signals["combined_signal"]
        confidence = signals["confidence"]

        self.last_signals[symbol] = signals

        # 캔들 마감 기준 진입/신호 판단: 같은 1시간봉을 10분마다 반복 매매하지 않도록 차단
        candle_ts = str(df.iloc[-1]["timestamp"]) if "timestamp" in df.columns else ""
        prev_ts = self.last_processed_candle_ts.get(symbol, "")
        is_new_candle = (candle_ts != "" and candle_ts != prev_ts)
        allow_entry_this_tick = (not Config.TRADE_ON_CANDLE_CLOSE_ONLY) or is_new_candle

        # 4. 현재 지표값 추출
        row = df.iloc[-1]
        indicators = {
            "ema20": round(row.get("ema20", 0), 6),
            "ema50": round(row.get("ema50", 0), 6),
            "rsi": round(row.get("rsi", 0), 2),
            "bb_pct": round(row.get("bb_pct", 0), 4),
            "adx": round(row.get("adx", 0), 2),
            "volume_ratio": round(row.get("volume_ratio", 0), 2),
        }
        self.last_indicators[symbol] = indicators

        # 5. 시그널 로그
        candle = {
            "open": round(row["open"], 6), "high": round(row["high"], 6),
            "low": round(row["low"], 6), "close": round(row["close"], 6),
            "volume": round(row["volume"], 2),
        }

        pos_info = mgr.get_position_info()
        current_position = None
        if pos_info:
            pnl = pct_change(pos_info["entry_price"], row["close"], pos_info["side"])
            current_position = {
                "side": pos_info["side"], "size": pos_info["size"],
                "entry_price": pos_info["entry_price"],
                "unrealized_pnl_pct": round(pnl, 2),
            }

        # 동시 포지션 보유 상태일 때는 진입 필터의 has_position을 False로 두고(추가진입은 별도 로직)
        filter_result = self.risk_mgr.check_entry_filters(df, False)

        action = "HOLD"
        if self.paused:
            action = "PAUSED"
        elif mgr.has_position():
            # 포지션 청산은 모니터 루프에서도 계속 체크하지만,
            # 신호 반전/시간 청산 같은 룰은 여기서도 확인한다.
            exit_reason = mgr.check_exit(row["close"], combined, indicators)
            if exit_reason:
                action = f"CLOSE_{exit_reason}"
        elif combined != 0 and filter_result["passed"]:
            can_trade, reason = self.risk_mgr.can_trade()
            if not allow_entry_this_tick:
                action = "WAIT_CANDLE_CLOSE"
            elif confidence < Config.MIN_ENTRY_CONFIDENCE:
                action = f"SKIP_LOW_CONF_{confidence}"
            elif can_trade:
                action = "OPEN_LONG" if combined == 1 else "OPEN_SHORT"
            else:
                action = f"BLOCKED_{reason}"

        signal_log = {
            "timestamp": timestamp_now(),
            "symbol": symbol,
            "candle": candle,
            "indicators": indicators,
            "signals": {k: signals[k] for k in ("MA", "RSI", "BB", "MTF")},
            "combined_signal": combined,
            "signal_detail": signals["signal_detail"],
            "filter_check": filter_result,
            "action": action,
            "current_position": current_position,
        }
        self.bot_logger.log_signal(signal_log)

        logger.info(f"SIGNAL [{symbol}]: {signals['signal_detail']} -> {action}")

        if self.paused:
            # 처리한 캔들 ts 기록 (중복 로그 방지)
            self.last_processed_candle_ts[symbol] = candle_ts
            return

        # 6. 포지션 보유 중 → 청산 체크 또는 피라미딩(추가진입)
        if mgr.has_position():
            exit_reason = mgr.check_exit(row["close"], combined, indicators)
            if exit_reason:
                mgr.close_position(row["close"], exit_reason, indicators)
                # 처리한 캔들 ts 기록
                self.last_processed_candle_ts[symbol] = candle_ts
                return

            # 피라미딩: 같은 방향 시그널 유지 + 현재 수익중일 때만 추가진입
            want_side = "Buy" if combined == 1 else "Sell" if combined == -1 else ""
            if want_side and want_side == mgr.side and mgr.add_count < Config.PYRAMID_MAX_ADDS:
                pnl_now = pct_change(mgr.entry_price, row["close"], mgr.side)
                if pnl_now >= Config.PYRAMID_MIN_PROFIT_PCT:
                    balance = self.exchange.get_balance()
                    equity = balance.get("totalEquity", 0)
                    avail = balance.get("availableBalance", 0)
                    qty_add, detail = self.risk_mgr.calc_qty_from_equity(
                        equity=equity,
                        confidence=int(signals.get("confidence", 2)),
                        mark_price=row["close"],
                        qty_step=mgr.qty_step,
                        min_qty=mgr.min_qty,
                        size_multiplier=Config.PYRAMID_ADD_SIZE_MULT,
                        available_balance=avail,
                    )
                    if qty_add > 0:
                        mgr.add_position(
                            current_price=row["close"],
                            signals=signals,
                            indicators=indicators,
                            qty_add=qty_add,
                        )

        # 7. 포지션 없음 → 시그널에 따라 진입
        elif combined != 0 and filter_result["passed"]:
            # 캔들 마감 기준 진입 (과매매 방지)
            if not allow_entry_this_tick:
                logger.info(f"SIGNAL [{symbol}]: 캔들 마감 대기 → 진입 스킵")
                self.last_processed_candle_ts[symbol] = candle_ts
                return

            # 최소 confidence 충족 (2/4 과반 진입 방지)
            if confidence < Config.MIN_ENTRY_CONFIDENCE:
                logger.info(
                    f"SIGNAL [{symbol}]: confidence({confidence}) < MIN_ENTRY_CONFIDENCE({Config.MIN_ENTRY_CONFIDENCE}) → 진입 스킵"
                )
                self.last_processed_candle_ts[symbol] = candle_ts
                return

            # 동시 오픈 포지션 제한
            active_positions = sum(1 for m in self.pos_managers.values() if m.has_position())
            if active_positions >= Config.MAX_OPEN_POSITIONS:
                logger.warning(f"RISK: 동시 포지션 제한({Config.MAX_OPEN_POSITIONS}) 도달 → {symbol} 진입 스킵")
                self.last_processed_candle_ts[symbol] = candle_ts
                return

            can_trade, reason = self.risk_mgr.can_trade()
            if can_trade:
                balance = self.exchange.get_balance()
                equity = balance.get("totalEquity", 0)
                avail = balance.get("availableBalance", 0)
                if equity > 0:
                    # P0: 전체 노출 한도 체크
                    current_margin_total = sum(
                        m.entry_price * m.qty / max(Config.LEVERAGE, 1)
                        for m in self.pos_managers.values() if m.has_position()
                    )
                    exposure_ok, exposure_reason = self.risk_mgr.check_total_exposure(
                        current_margin_total, equity
                    )
                    if not exposure_ok:
                        logger.warning(f"SIGNAL [{symbol}]: 전체 노출 한도 초과 → 진입 스킵 — {exposure_reason}")
                    else:
                        side = "Buy" if combined == 1 else "Sell"
                        qty, detail = self.risk_mgr.calc_qty_from_equity(
                            equity=equity,
                            confidence=int(signals.get("confidence", 2)),
                            mark_price=row["close"],
                            qty_step=mgr.qty_step,
                            min_qty=mgr.min_qty,
                            available_balance=avail,
                        )
                        if qty > 0:
                            mgr.open_position(
                                side=side, margin_usdt=0,
                                current_price=row["close"],
                                signals=signals, indicators=indicators,
                                qty_override=qty,
                            )
                        else:
                            reason = detail.get('reason', 'unknown')
                            logger.warning(f"SIGNAL [{symbol}]: 수량 계산 불가 — {reason}")
                else:
                    logger.error(f"SIGNAL [{symbol}]: equity 0, 진입 불가")
            else:
                logger.info(f"SIGNAL [{symbol}]: 매매 차단 - {reason}")

            # 처리한 캔들 ts 기록
            self.last_processed_candle_ts[symbol] = candle_ts

    def _monitor_position(self, symbol: str, mgr: PositionManager):
        """포지션 실시간 모니터링 (10초 간격)."""
        try:
            mgr.sync_with_exchange()
            if not mgr.has_position():
                return

            ticker = self.exchange.get_ticker(symbol=symbol)
            current_price = ticker.get("last_price", 0)
            if current_price <= 0:
                return

            exit_reason = mgr.check_exit(current_price, 0, {})
            if exit_reason:
                logger.info(f"MONITOR [{symbol}]: 청산 트리거 - {exit_reason}")
                mgr.close_position(current_price, exit_reason, {})

        except Exception as e:
            logger.error(f"MONITOR_ERROR [{symbol}]: {e}")

    def _log_equity(self):
        """잔고 데이터 로그."""
        try:
            balance = self.exchange.get_balance()
            equity = balance.get("totalEquity", 0)

            today_trades = self.bot_logger.get_today_trades()
            realized_today = sum(t.get("net_pnl_usdt", 0) for t in today_trades)

            all_trades = self.bot_logger.get_recent_trades(limit=200)
            cumulative = sum(t.get("net_pnl_usdt", 0) for t in all_trades)

            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent = [t for t in all_trades if t.get("timestamp_close", "") >= seven_days_ago]
            wins_7d = sum(1 for t in recent if t.get("net_pnl_pct", 0) > 0)
            win_rate_7d = (wins_7d / len(recent) * 100) if recent else 0

            # 활성 포지션 수
            active_positions = sum(1 for mgr in self.pos_managers.values() if mgr.has_position())

            self.bot_logger.log_equity({
                "timestamp": timestamp_now(),
                "total_equity": round(equity, 2),
                "available_balance": round(balance.get("availableBalance", 0), 2),
                "position_margin": round(equity - balance.get("availableBalance", 0), 2),
                "realized_pnl_today": round(realized_today, 2),
                "cumulative_pnl": round(cumulative, 2),
                "num_trades_today": len(today_trades),
                "win_rate_7d": round(win_rate_7d, 1),
                "active_positions": active_positions,
            })
        except Exception as e:
            logger.error(f"EQUITY_LOG_ERROR: {e}")

    def _daily_summary(self):
        """일일 서머리."""
        try:
            balance = self.exchange.get_balance()
            equity = balance.get("totalEquity", 0)
            today_trades = self.bot_logger.get_today_trades()
            realized_today = sum(t.get("net_pnl_usdt", 0) for t in today_trades)

            # 활성 포지션
            pos_lines = []
            total_unrealized = 0
            for sym, mgr in self.pos_managers.items():
                pos = self.exchange.get_position(symbol=sym)
                if pos:
                    name = sym.replace("USDT", "")
                    upnl = pos.get("unrealized_pnl", 0)
                    total_unrealized += upnl
                    direction = "L" if pos["side"] == "Buy" else "S"
                    pos_lines.append(f"  {name} {direction} ${upnl:+.2f}")

            all_trades = self.bot_logger.get_recent_trades(limit=200)
            cumulative = sum(t.get("net_pnl_usdt", 0) for t in all_trades)
            initial_equity = equity - cumulative if cumulative else equity
            equity_change_pct = ((equity - initial_equity) / initial_equity * 100) if initial_equity > 0 else 0

            pos_str = "\n".join(pos_lines) if pos_lines else "  없음"
            current_position = {"details": pos_str} if pos_lines else None

            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent = [t for t in all_trades if t.get("timestamp_close", "") >= seven_days_ago]
            wins = [t for t in recent if t.get("net_pnl_pct", 0) > 0]
            losses = [t for t in recent if t.get("net_pnl_pct", 0) <= 0]
            avg_win = sum(t.get("net_pnl_pct", 0) for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.get("net_pnl_pct", 0) for t in losses) / len(losses) if losses else 0
            total_wins_usd = sum(t.get("net_pnl_usdt", 0) for t in wins)
            total_losses_usd = abs(sum(t.get("net_pnl_usdt", 0) for t in losses))
            pf = total_wins_usd / total_losses_usd if total_losses_usd > 0 else 0

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
                unrealized_pnl=total_unrealized,
                trades_today=today_trades,
                current_position=current_position,
                stats_7d=stats_7d,
            )
            self.notifier.notify_daily_summary(summary)
            logger.info("DAILY_SUMMARY: 발송 완료")

        except Exception as e:
            logger.error(f"DAILY_SUMMARY_ERROR: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # 전략 리뷰 (일일 1회 자동 + /리뷰 수동)
    # ──────────────────────────────────────────────

    def _cmd_review(self, args: str) -> str:
        self._daily_chart_analysis()
        self._daily_strategy_review()
        return "\U0001f9e0 차트 분석 + 전략 리뷰 완료 - 결과가 별도 메시지로 전송됩니다"

    def _cmd_approve(self, args: str) -> str:
        if not self.pending_suggestions:
            return "\u274c 적용할 추천 사항이 없습니다. /리뷰 로 분석을 먼저 실행하세요"

        target = args.strip()
        if not target:
            return "\u274c 사용법: /승인 1 (번호) 또는 /승인 전체"

        if target in ("전체", "all", "모두"):
            results = []
            for sug in self.pending_suggestions:
                ok = self._apply_suggestion(sug)
                status = "\u2705" if ok else "\u274c"
                results.append(f"{status} #{sug['id']}: {sug['short']}")
            self.pending_suggestions = []
            return "\U0001f527 추천 적용 결과:\n" + "\n".join(results)

        try:
            idx = int(target)
        except ValueError:
            return f"\u274c 숫자를 입력하세요: /승인 1"

        sug = next((s for s in self.pending_suggestions if s["id"] == idx), None)
        if not sug:
            ids = ", ".join(str(s["id"]) for s in self.pending_suggestions)
            return f"\u274c #{idx} 없음. 가능한 번호: {ids}"

        ok = self._apply_suggestion(sug)
        self.pending_suggestions = [s for s in self.pending_suggestions if s["id"] != idx]

        if ok:
            remaining = len(self.pending_suggestions)
            extra = f"\n남은 추천: {remaining}개" if remaining > 0 else ""
            return f"\u2705 #{idx} 적용 완료: {sug['short']}{extra}"
        return f"\u274c #{idx} 적용 실패"

    def _apply_suggestion(self, sug: dict) -> bool:
        """추천 사항을 실제로 적용."""
        try:
            action_type = sug["action_type"]
            action_val = sug["action_val"]

            if action_type == "STOP_LOSS_PCT":
                Config.STOP_LOSS_PCT = action_val
                logger.info(f"STRATEGY_REVIEW: SL 변경 → {action_val}%")
                return True
            elif action_type == "TAKE_PROFIT_PCT":
                Config.TAKE_PROFIT_PCT = action_val
                logger.info(f"STRATEGY_REVIEW: TP 변경 → {action_val}%")
                return True
            elif action_type == "TRAILING_STOP_ACTIVATE_PCT":
                Config.TRAILING_STOP_ACTIVATE_PCT = action_val
                logger.info(f"STRATEGY_REVIEW: 트레일링 활성 변경 → {action_val}%")
                return True
            elif action_type == "TRAILING_STOP_CALLBACK_PCT":
                Config.TRAILING_STOP_CALLBACK_PCT = action_val
                logger.info(f"STRATEGY_REVIEW: 트레일링 콜백 변경 → {action_val}%")
                return True
            elif action_type == "POSITION_SIZE_PCT":
                Config.POSITION_SIZE_PCT = action_val
                logger.info(f"STRATEGY_REVIEW: 포지션 사이즈 변경 → {action_val}%")
                return True
            elif action_type == "LEVERAGE":
                Config.LEVERAGE = int(action_val)
                for sym in self.symbols:
                    self.exchange.setup_leverage(sym, int(action_val))
                logger.info(f"STRATEGY_REVIEW: 레버리지 변경 → {int(action_val)}x")
                return True
            elif action_type == "REMOVE_SYMBOL":
                sym = action_val
                if sym in self.symbols:
                    self.symbols.remove(sym)
                    Config.SYMBOLS = self.symbols
                    if sym in self.pos_managers:
                        del self.pos_managers[sym]
                    logger.info(f"STRATEGY_REVIEW: {sym} 제거")
                    return True
            elif action_type == "MIN_VOLUME_RATIO":
                Config.MIN_VOLUME_RATIO = action_val
                logger.info(f"STRATEGY_REVIEW: 거래량 필터 변경 → {action_val}")
                return True

            return False
        except Exception as e:
            logger.error(f"APPLY_SUGGESTION_ERROR: {e}")
            return False

    def _daily_chart_analysis(self):
        """일일 차트 분석 - 모든 코인 기술적 분석 리포트."""
        logger.info("CHART_ANALYSIS: 시작")
        try:
            report_lines = [
                "\U0001f4c8 <b>일일 차트 분석 (KST 09:00)</b>",
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
            ]

            for sym in self.symbols:
                name = sym.replace("USDT", "")
                try:
                    df = self.exchange.get_klines(symbol=sym)
                    if df.empty:
                        report_lines.append(f"\n\u274c <b>{name}</b>: 데이터 없음")
                        continue

                    df = calc_all_indicators(df)
                    row = df.iloc[-1]

                    # 현재가 + 24시간 변화
                    close = row["close"]
                    close_24h_ago = df.iloc[-24]["close"] if len(df) >= 24 else df.iloc[0]["close"]
                    change_24h = ((close - close_24h_ago) / close_24h_ago) * 100

                    # 추세 판단
                    ema20 = row.get("ema20", 0)
                    ema50 = row.get("ema50", 0)
                    ema200 = row.get("ema200", 0)
                    adx = row.get("adx", 0)

                    if ema20 > ema50 > ema200:
                        trend = "\U0001f7e2 강한 상승"
                    elif ema20 > ema50:
                        trend = "\U0001f7e2 상승"
                    elif ema20 < ema50 < ema200:
                        trend = "\U0001f534 강한 하락"
                    elif ema20 < ema50:
                        trend = "\U0001f534 하락"
                    else:
                        trend = "\U0001f7e1 횡보"

                    if adx < 20:
                        trend += " (약한 추세)"
                    elif adx > 40:
                        trend += " (강한 추세)"

                    # RSI 상태
                    rsi = row.get("rsi", 50)
                    if rsi > 70:
                        rsi_str = f"\U0001f534 과매수 ({rsi:.0f})"
                    elif rsi > 60:
                        rsi_str = f"\U0001f7e1 매수우세 ({rsi:.0f})"
                    elif rsi < 30:
                        rsi_str = f"\U0001f7e2 과매도 ({rsi:.0f})"
                    elif rsi < 40:
                        rsi_str = f"\U0001f7e1 매도우세 ({rsi:.0f})"
                    else:
                        rsi_str = f"\u26aa 중립 ({rsi:.0f})"

                    # 볼린저밴드 상태
                    bb_pct = row.get("bb_pct", 0.5)
                    bb_width = row.get("bb_width", 0)
                    bb_upper = row.get("bb_upper", 0)
                    bb_lower = row.get("bb_lower", 0)

                    # 스퀴즈 감지
                    recent_widths = df["bb_width"].tail(50)
                    squeeze_threshold = recent_widths.quantile(0.2) if len(recent_widths) >= 50 else 0
                    is_squeeze = bb_width <= squeeze_threshold

                    if is_squeeze:
                        bb_str = "\u26a1 스퀴즈 (돌파 임박)"
                    elif bb_pct > 0.95:
                        bb_str = "\U0001f534 상단밴드 (과열)"
                    elif bb_pct < 0.05:
                        bb_str = "\U0001f7e2 하단밴드 (반등 가능)"
                    else:
                        bb_str = f"밴드 위치 {bb_pct*100:.0f}%"

                    # 지지/저항 수준 (최근 50봉 고저)
                    recent_50 = df.tail(50)
                    resistance = recent_50["high"].max()
                    support = recent_50["low"].min()
                    pivot = (resistance + support + close) / 3

                    # 거래량
                    vol_ratio = row.get("volume_ratio", 1.0)
                    if vol_ratio > 2.0:
                        vol_str = f"\U0001f4a5 폭증 ({vol_ratio:.1f}x)"
                    elif vol_ratio > 1.3:
                        vol_str = f"\U0001f4c8 증가 ({vol_ratio:.1f}x)"
                    elif vol_ratio < 0.5:
                        vol_str = f"\U0001f4c9 감소 ({vol_ratio:.1f}x)"
                    else:
                        vol_str = f"보통 ({vol_ratio:.1f}x)"

                    # 4H 추세 (MTF)
                    ema20_4h = row.get("ema20_4h", 0)
                    ema50_4h = row.get("ema50_4h", 0)
                    if ema20_4h > ema50_4h:
                        mtf_str = "\U0001f7e2 4H 상승"
                    elif ema20_4h < ema50_4h:
                        mtf_str = "\U0001f534 4H 하락"
                    else:
                        mtf_str = "\U0001f7e1 4H 중립"

                    # 종합 전망
                    bull_count = 0
                    bear_count = 0
                    if ema20 > ema50:
                        bull_count += 1
                    else:
                        bear_count += 1
                    if rsi < 45:
                        bear_count += 1
                    elif rsi > 55:
                        bull_count += 1
                    if bb_pct < 0.3:
                        bull_count += 1  # 하단 → 반등 기대
                    elif bb_pct > 0.7:
                        bear_count += 1  # 상단 → 조정 기대
                    if ema20_4h > ema50_4h:
                        bull_count += 1
                    else:
                        bear_count += 1

                    if bull_count >= 3:
                        outlook = "\U0001f7e2 매수 유리"
                    elif bear_count >= 3:
                        outlook = "\U0001f534 매도 유리"
                    else:
                        outlook = "\U0001f7e1 관망"

                    change_icon = "\U0001f4c8" if change_24h >= 0 else "\U0001f4c9"

                    report_lines.append(
                        f"\n<b>{name}</b> ${close:.4f} ({change_icon}{change_24h:+.1f}%)\n"
                        f"  추세: {trend} | ADX: {adx:.0f}\n"
                        f"  RSI: {rsi_str}\n"
                        f"  BB: {bb_str}\n"
                        f"  거래량: {vol_str} | {mtf_str}\n"
                        f"  지지: ${support:.4f} | 저항: ${resistance:.4f}\n"
                        f"  \U0001f3af 전망: {outlook}"
                    )

                except Exception as e:
                    report_lines.append(f"\n\u274c <b>{name}</b>: 분석 에러 - {e}")

            report = "\n".join(report_lines)
            self.notifier.send(report)
            logger.info("CHART_ANALYSIS: 완료")

        except Exception as e:
            logger.error(f"CHART_ANALYSIS_ERROR: {e}", exc_info=True)
            self.notifier.notify_warning(f"차트 분석 에러: {e}")

    def _daily_strategy_review(self):
        """일일 전략 리뷰 - 매매 기록 분석 + 개선 추천."""
        logger.info("STRATEGY_REVIEW: 시작")
        try:
            trades = self.bot_logger.get_recent_trades(limit=200)
            # 최근 7일 매매만
            seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            trades_7d = [t for t in trades if t.get("timestamp_close", "") >= seven_days_ago]

            if len(trades_7d) < 3:
                msg = (
                    "\U0001f9e0 <b>일일 전략 리뷰</b>\n"
                    "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                    f"최근 7일 매매 {len(trades_7d)}건 - 분석에 충분하지 않습니다.\n"
                    "최소 3건 이상 필요합니다."
                )
                self.notifier.send(msg)
                return

            # ── 분석 시작 ──
            total = len(trades_7d)
            wins = [t for t in trades_7d if t.get("net_pnl_pct", 0) > 0]
            losses = [t for t in trades_7d if t.get("net_pnl_pct", 0) <= 0]
            win_rate = len(wins) / total * 100
            total_pnl = sum(t.get("net_pnl_usdt", 0) for t in trades_7d)
            avg_win = sum(t.get("net_pnl_pct", 0) for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.get("net_pnl_pct", 0) for t in losses) / len(losses) if losses else 0

            # 청산 사유별 분석
            exit_counts: dict[str, int] = {}
            for t in trades_7d:
                reason = t.get("exit_reason", "UNKNOWN")
                exit_counts[reason] = exit_counts.get(reason, 0) + 1

            sl_count = exit_counts.get("SL_HIT", 0) + exit_counts.get("SERVER_SL", 0)
            tp_count = exit_counts.get("TP_HIT", 0) + exit_counts.get("SERVER_TP", 0)
            trailing_count = exit_counts.get("TRAILING_STOP", 0)
            signal_count = exit_counts.get("SIGNAL_REVERSE", 0)

            sl_rate = sl_count / total * 100
            tp_rate = tp_count / total * 100

            # 코인별 분석
            coin_stats: dict[str, dict] = {}
            for t in trades_7d:
                sym = t.get("symbol", "XRPUSDT")
                if sym not in coin_stats:
                    coin_stats[sym] = {"total": 0, "wins": 0, "pnl": 0.0}
                coin_stats[sym]["total"] += 1
                if t.get("net_pnl_pct", 0) > 0:
                    coin_stats[sym]["wins"] += 1
                coin_stats[sym]["pnl"] += t.get("net_pnl_usdt", 0)

            # 지표별 정확도 분석
            indicator_accuracy: dict[str, dict] = {}
            for ind_name in ("MA", "RSI", "BB", "MTF"):
                correct = 0
                wrong = 0
                neutral = 0
                for t in trades_7d:
                    sigs = t.get("signals_at_entry", {})
                    val = sigs.get(ind_name, 0)
                    if isinstance(val, dict):
                        val = val.get("value", 0)
                    if val == 0:
                        neutral += 1
                        continue
                    trade_won = t.get("net_pnl_pct", 0) > 0
                    if trade_won:
                        correct += 1
                    else:
                        wrong += 1
                participated = correct + wrong
                accuracy = (correct / participated * 100) if participated > 0 else 0
                indicator_accuracy[ind_name] = {
                    "correct": correct, "wrong": wrong,
                    "neutral": neutral, "accuracy": accuracy,
                    "participated": participated,
                }

            # 평균 보유 시간
            avg_hold_win = sum(t.get("holding_hours", 0) for t in wins) / len(wins) if wins else 0
            avg_hold_loss = sum(t.get("holding_hours", 0) for t in losses) / len(losses) if losses else 0

            # ── 리포트 생성 ──
            report_lines = [
                "\U0001f9e0 <b>일일 전략 리뷰</b>",
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
                f"\U0001f4ca <b>7일 성과</b> ({total}매매)",
                f"  승률: {win_rate:.0f}% ({len(wins)}승 {len(losses)}패)",
                f"  총 PnL: ${total_pnl:+.2f}",
                f"  평균 수익: +{avg_win:.2f}% | 평균 손실: {avg_loss:.2f}%",
                "",
                f"\U0001f3af <b>청산 분석</b>",
                f"  SL: {sl_count}건 ({sl_rate:.0f}%) | TP: {tp_count}건 ({tp_rate:.0f}%)",
                f"  트레일링: {trailing_count}건 | 시그널반전: {signal_count}건",
                f"  평균 보유: 수익 {avg_hold_win:.1f}h / 손실 {avg_hold_loss:.1f}h",
                "",
            ]

            # 코인별 성과
            report_lines.append("\U0001f4b0 <b>코인별 성과</b>")
            for sym, st in sorted(coin_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
                name = sym.replace("USDT", "")
                wr = st["wins"] / st["total"] * 100 if st["total"] > 0 else 0
                icon = "\u2705" if st["pnl"] > 0 else "\u274c"
                report_lines.append(
                    f"  {icon} {name}: {wr:.0f}% 승률 ({st['total']}건) ${st['pnl']:+.2f}"
                )

            # 지표별 정확도
            report_lines.append("")
            report_lines.append("\U0001f50d <b>지표 정확도</b>")
            for ind_name in ("MA", "RSI", "BB", "MTF"):
                ia = indicator_accuracy[ind_name]
                if ia["participated"] > 0:
                    icon = "\u2705" if ia["accuracy"] >= 50 else "\u26a0\ufe0f"
                    report_lines.append(
                        f"  {icon} {ind_name}: {ia['accuracy']:.0f}% "
                        f"({ia['correct']}/{ia['participated']}건 적중, {ia['neutral']}건 중립)"
                    )
                else:
                    report_lines.append(f"  \u2b1c {ind_name}: 데이터 없음")

            # ── 추천 생성 ──
            self.pending_suggestions = []
            suggestion_id = 0

            # 1. SL 너무 타이트?
            if sl_rate > 45 and total >= 5:
                new_sl = round(Config.STOP_LOSS_PCT + 0.5, 1)
                if new_sl <= 5.0:
                    suggestion_id += 1
                    self.pending_suggestions.append({
                        "id": suggestion_id,
                        "short": f"SL {Config.STOP_LOSS_PCT}% \u2192 {new_sl}%",
                        "desc": f"SL 확대 추천: SL 적중률 {sl_rate:.0f}%로 과다. {Config.STOP_LOSS_PCT}% \u2192 {new_sl}%",
                        "action_type": "STOP_LOSS_PCT",
                        "action_val": new_sl,
                    })

            # 2. TP 너무 높음?
            if tp_rate < 15 and total >= 5 and Config.TAKE_PROFIT_PCT > 2.0:
                new_tp = round(Config.TAKE_PROFIT_PCT - 1.0, 1)
                if new_tp >= 2.0:
                    suggestion_id += 1
                    self.pending_suggestions.append({
                        "id": suggestion_id,
                        "short": f"TP {Config.TAKE_PROFIT_PCT}% \u2192 {new_tp}%",
                        "desc": f"TP 축소 추천: TP 적중률 {tp_rate:.0f}%로 너무 낮음. {Config.TAKE_PROFIT_PCT}% \u2192 {new_tp}%",
                        "action_type": "TAKE_PROFIT_PCT",
                        "action_val": new_tp,
                    })

            # 3. TP가 너무 쉽게 달성됨 (대부분 TP)
            if tp_rate > 70 and total >= 5 and Config.TAKE_PROFIT_PCT < 8.0:
                new_tp = round(Config.TAKE_PROFIT_PCT + 1.0, 1)
                suggestion_id += 1
                self.pending_suggestions.append({
                    "id": suggestion_id,
                    "short": f"TP {Config.TAKE_PROFIT_PCT}% \u2192 {new_tp}%",
                    "desc": f"TP 확대 추천: TP 적중률 {tp_rate:.0f}%로 높음, 더 큰 수익 가능. {Config.TAKE_PROFIT_PCT}% \u2192 {new_tp}%",
                    "action_type": "TAKE_PROFIT_PCT",
                    "action_val": new_tp,
                })

            # 4. 성적 나쁜 코인 제거 추천
            for sym, st in coin_stats.items():
                if st["total"] >= 3:
                    wr = st["wins"] / st["total"] * 100
                    if wr < 25 and st["pnl"] < 0:
                        name = sym.replace("USDT", "")
                        suggestion_id += 1
                        self.pending_suggestions.append({
                            "id": suggestion_id,
                            "short": f"{name} 제거",
                            "desc": f"{name} 제거 추천: 승률 {wr:.0f}% ({st['total']}건), PnL ${st['pnl']:+.2f}",
                            "action_type": "REMOVE_SYMBOL",
                            "action_val": sym,
                        })

            # 5. 승률 높으면 사이즈 증가 추천
            if win_rate > 65 and total >= 10 and total_pnl > 0:
                if Config.POSITION_SIZE_PCT < 8:
                    new_size = round(Config.POSITION_SIZE_PCT + 1, 1)
                    suggestion_id += 1
                    self.pending_suggestions.append({
                        "id": suggestion_id,
                        "short": f"사이즈 {Config.POSITION_SIZE_PCT}% \u2192 {new_size}%",
                        "desc": f"포지션 사이즈 증가 추천: 승률 {win_rate:.0f}%, 수익 ${total_pnl:+.2f}",
                        "action_type": "POSITION_SIZE_PCT",
                        "action_val": new_size,
                    })

            # 6. 승률 낮으면 사이즈 감소 추천
            if win_rate < 40 and total >= 5 and total_pnl < 0:
                if Config.POSITION_SIZE_PCT > 3:
                    new_size = round(Config.POSITION_SIZE_PCT - 1, 1)
                    suggestion_id += 1
                    self.pending_suggestions.append({
                        "id": suggestion_id,
                        "short": f"사이즈 {Config.POSITION_SIZE_PCT}% \u2192 {new_size}%",
                        "desc": f"포지션 사이즈 축소 추천: 승률 {win_rate:.0f}%, 손실 ${total_pnl:+.2f}",
                        "action_type": "POSITION_SIZE_PCT",
                        "action_val": new_size,
                    })

            # 7. 트레일링 스탑 조정
            if trailing_count == 0 and total >= 10:
                new_trail = round(Config.TRAILING_STOP_ACTIVATE_PCT - 0.5, 1)
                if new_trail >= 1.0:
                    suggestion_id += 1
                    self.pending_suggestions.append({
                        "id": suggestion_id,
                        "short": f"트레일링 {Config.TRAILING_STOP_ACTIVATE_PCT}% \u2192 {new_trail}%",
                        "desc": f"트레일링 활성 기준 완화: 트레일링 0회 발동, 기준을 낮춰 수익 보호",
                        "action_type": "TRAILING_STOP_ACTIVATE_PCT",
                        "action_val": new_trail,
                    })

            # ── 추천 사항 표시 ──
            if self.pending_suggestions:
                report_lines.append("")
                report_lines.append("\U0001f527 <b>추천 변경 사항</b>")
                for sug in self.pending_suggestions:
                    report_lines.append(f"  <b>#{sug['id']}</b> {sug['desc']}")
                report_lines.append("")
                report_lines.append("\U0001f449 /승인 1 (번호) 또는 /승인 전체")
            else:
                report_lines.append("")
                report_lines.append("\u2705 현재 전략 설정 적정 - 변경 추천 없음")

            report = "\n".join(report_lines)
            self.notifier.send(report)
            logger.info(f"STRATEGY_REVIEW: 완료 ({len(self.pending_suggestions)}개 추천)")

        except Exception as e:
            logger.error(f"STRATEGY_REVIEW_ERROR: {e}", exc_info=True)
            self.notifier.notify_warning(f"전략 리뷰 에러: {e}")

    def _shutdown(self, reason: str):
        self.running = False
        logger.info(f"BOT_SHUTDOWN: {reason}")
        self.notifier.notify_critical(f"봇 종료: {reason}")


def main():
    parser = argparse.ArgumentParser(description="멀티코인 자동매매 봇")
    parser.add_argument("--testnet", action="store_true", help="테스트넷 모드")
    args = parser.parse_args()

    if args.testnet:
        Config.set_testnet(True)
        print("[INFO] 테스트넷 모드로 실행")
    elif not Config.BYBIT_TESTNET:
        print("[WARNING] \u26a0\ufe0f  실전(LIVE) 모드로 실행합니다!")

    errors = Config.validate()
    if errors:
        for e in errors:
            print(f"[ERROR] {e}")
        sys.exit(1)

    bot = TradingBot()

    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        bot._shutdown(f"Signal {sig_name}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    bot.run()


if __name__ == "__main__":
    main()
