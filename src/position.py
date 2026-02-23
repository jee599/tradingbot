"""포지션 관리 모듈 - 진입, 청산, 트레일링 스탑."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import Config
from src.exchange import BybitExchange
from src.risk_manager import RiskManager
from src.logger import BotLogger
from src.telegram_bot import TelegramNotifier
from src.utils import generate_trade_id, pct_change, round_qty, timestamp_now

logger = logging.getLogger("xrp_bot")


class PositionManager:
    """포지션 관리 (진입/청산/트레일링)."""

    def __init__(self, exchange: BybitExchange, risk_mgr: RiskManager,
                 bot_logger: BotLogger, notifier: TelegramNotifier):
        self.exchange = exchange
        self.risk_mgr = risk_mgr
        self.bot_logger = bot_logger
        self.notifier = notifier

        # 현재 관리 중인 포지션 상태
        self.trade_id: str | None = None
        self.entry_time: datetime | None = None
        self.entry_price: float = 0.0
        self.side: str = ""
        self.qty: float = 0.0
        self.signals_at_entry: dict = {}
        self.indicators_at_entry: dict = {}

        # 트레일링 스탑
        self.trailing_active: bool = False
        self.trailing_high: float = 0.0  # 롱 고점 / 숏 저점

        # 수수료 추적
        self.fee_rate: float = 0.00055  # Bybit taker fee 0.055%

    def open_position(self, side: str, margin_usdt: float, current_price: float,
                      signals: dict, indicators: dict) -> bool:
        """포지션 진입.

        Args:
            side: "Buy" (롱) 또는 "Sell" (숏)
            margin_usdt: 투입 마진 (USDT)
            current_price: 현재가
            signals: 진입 시점 시그널
            indicators: 진입 시점 지표값

        Returns:
            성공 여부
        """
        position_value = margin_usdt * Config.LEVERAGE
        qty = round_qty(position_value / current_price, 1.0)

        if qty <= 0:
            logger.error("POSITION: 수량 0 이하, 진입 불가")
            return False

        result = self.exchange.place_order(side, qty)
        if result is None:
            return False

        # 상태 저장
        self.trade_id = generate_trade_id()
        self.entry_time = datetime.now(timezone.utc)
        self.entry_price = current_price
        self.side = side
        self.qty = qty
        self.signals_at_entry = signals
        self.indicators_at_entry = indicators
        self.trailing_active = False
        self.trailing_high = current_price

        direction = "Long" if side == "Buy" else "Short"
        sl_price = self._calc_sl_price()
        tp_price = self._calc_tp_price()

        logger.info(
            f"POSITION_OPEN: {direction} {qty} XRP @ ${current_price:.4f} | "
            f"마진: ${margin_usdt:.2f} | SL: ${sl_price:.4f} | TP: ${tp_price:.4f}"
        )

        confidence = signals.get("confidence", 0)
        signal_values = {
            k: v.get("value", 0) if isinstance(v, dict) else v
            for k, v in signals.items()
            if k in ("MA", "RSI", "BB", "MTF")
        }

        self.notifier.notify_entry(
            side=side, price=current_price, qty=qty, leverage=Config.LEVERAGE,
            sl=sl_price, tp=tp_price,
            sl_pct=Config.STOP_LOSS_PCT, tp_pct=Config.TAKE_PROFIT_PCT,
            signals=signal_values, confidence=confidence,
        )

        return True

    def check_exit(self, current_price: float, combined_signal: int,
                   current_indicators: dict) -> str | None:
        """청산 조건 확인.

        Args:
            current_price: 현재가
            combined_signal: 현재 조합 시그널 (-1, 0, 1)
            current_indicators: 현재 지표값

        Returns:
            청산 사유 문자열 또는 None (유지)
        """
        if not self.side:
            return None

        pnl = pct_change(self.entry_price, current_price, self.side)

        # 1. 손절 (Stop Loss): -2.0%
        if pnl <= -Config.STOP_LOSS_PCT:
            return "SL_HIT"

        # 2. 익절 (Take Profit): +4.0%
        if pnl >= Config.TAKE_PROFIT_PCT:
            return "TP_HIT"

        # 3. 트레일링 스탑
        if pnl >= Config.TRAILING_STOP_ACTIVATE_PCT:
            if not self.trailing_active:
                self.trailing_active = True
                self.trailing_high = current_price
                logger.info(f"TRAILING_STOP: 활성화 (PnL: +{pnl:.2f}%)")

        if self.trailing_active:
            if self.side == "Buy":
                if current_price > self.trailing_high:
                    self.trailing_high = current_price
                drawdown = pct_change(self.trailing_high, current_price, "Buy")
            else:  # Sell (숏)
                if current_price < self.trailing_high:
                    self.trailing_high = current_price
                drawdown = pct_change(self.trailing_high, current_price, "Sell")

            if drawdown <= -Config.TRAILING_STOP_CALLBACK_PCT:
                return "TRAILING_STOP"

        # 4. 시그널 반전 청산
        if self.side == "Buy" and combined_signal == -1:
            return "SIGNAL_REVERSE"
        if self.side == "Sell" and combined_signal == 1:
            return "SIGNAL_REVERSE"

        # 5. 시간 기반 청산 (48시간 + 손실 중)
        if self.entry_time:
            hours_held = (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600
            if hours_held >= Config.TIME_EXIT_HOURS and pnl < 0:
                return "TIME_EXIT"

        return None

    def close_position(self, current_price: float, exit_reason: str,
                       current_indicators: dict) -> dict | None:
        """포지션 청산 실행.

        Returns:
            매매 기록 dict 또는 None.
        """
        if not self.side:
            return None

        result = self.exchange.close_position(self.side, self.qty)
        if result is None:
            logger.error("POSITION: 청산 주문 실패")
            return None

        # PnL 계산
        pnl_pct = pct_change(self.entry_price, current_price, self.side)
        position_value = self.entry_price * self.qty
        pnl_usdt = position_value * (pnl_pct / 100)
        fee_entry = position_value * self.fee_rate
        fee_exit = current_price * self.qty * self.fee_rate
        fee_total = fee_entry + fee_exit
        net_pnl_usdt = pnl_usdt - fee_total
        net_pnl_pct = pnl_pct - (fee_total / (position_value / Config.LEVERAGE)) * 100

        now = datetime.now(timezone.utc)
        holding_hours = (now - self.entry_time).total_seconds() / 3600 if self.entry_time else 0

        direction = "Long" if self.side == "Buy" else "Short"

        # 매매 기록 생성
        trade_data = {
            "trade_id": self.trade_id,
            "timestamp_open": self.entry_time.isoformat() if self.entry_time else "",
            "timestamp_close": timestamp_now(),
            "symbol": Config.SYMBOL,
            "side": self.side,
            "direction": direction,
            "entry_price": self.entry_price,
            "exit_price": current_price,
            "quantity": self.qty,
            "leverage": Config.LEVERAGE,
            "position_value_usdt": position_value,
            "margin_used_usdt": position_value / Config.LEVERAGE,
            "pnl_usdt": round(pnl_usdt, 4),
            "pnl_pct": round(pnl_pct, 4),
            "fee_entry_usdt": round(fee_entry, 4),
            "fee_exit_usdt": round(fee_exit, 4),
            "fee_total_usdt": round(fee_total, 4),
            "net_pnl_usdt": round(net_pnl_usdt, 4),
            "net_pnl_pct": round(net_pnl_pct, 4),
            "exit_reason": exit_reason,
            "holding_hours": round(holding_hours, 2),
            "signals_at_entry": {
                k: v.get("value", 0) if isinstance(v, dict) else v
                for k, v in self.signals_at_entry.items()
            },
            "indicators_at_entry": self.indicators_at_entry,
            "indicators_at_exit": current_indicators,
            "risk_metrics": {
                "max_drawdown_during_trade_pct": round(-Config.STOP_LOSS_PCT if exit_reason == "SL_HIT" else min(0, pnl_pct), 2),
                "max_profit_during_trade_pct": round(max(0, pnl_pct), 2),
                "trailing_stop_activated": self.trailing_active,
                "trailing_stop_high": round(self.trailing_high, 6),
            },
        }

        # 로깅
        self.bot_logger.log_trade(trade_data)
        self.risk_mgr.record_trade(net_pnl_pct, exit_reason)

        logger.info(
            f"POSITION_CLOSE: {direction} | {exit_reason} | "
            f"PnL: {pnl_pct:+.2f}% | Net: ${net_pnl_usdt:+.2f} | "
            f"보유: {holding_hours:.1f}h"
        )

        self.notifier.notify_exit(
            exit_reason=exit_reason, pnl_pct=pnl_pct,
            net_pnl=net_pnl_usdt, fee_total=fee_total,
            holding_hours=holding_hours,
        )

        # 상태 초기화
        self._reset()

        return trade_data

    def sync_with_exchange(self):
        """거래소 포지션과 내부 상태 동기화."""
        pos = self.exchange.get_position()
        if pos is None:
            if self.side:
                logger.warning("POSITION: 거래소에 포지션 없음, 내부 상태 초기화")
                self._reset()
        else:
            if not self.side:
                # 외부에서 진입된 포지션 감지
                logger.warning(
                    f"POSITION: 외부 포지션 감지 - {pos['side']} {pos['size']} @ {pos['entry_price']}"
                )
                self.side = pos["side"]
                self.qty = pos["size"]
                self.entry_price = pos["entry_price"]
                self.entry_time = datetime.now(timezone.utc)
                self.trade_id = generate_trade_id()

    def has_position(self) -> bool:
        """포지션 보유 여부."""
        return bool(self.side)

    def get_position_info(self) -> dict | None:
        """현재 포지션 정보."""
        if not self.side:
            return None
        return {
            "trade_id": self.trade_id,
            "side": self.side,
            "size": self.qty,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "trailing_active": self.trailing_active,
            "trailing_high": self.trailing_high,
        }

    def _calc_sl_price(self) -> float:
        """손절가 계산."""
        if self.side == "Buy":
            return self.entry_price * (1 - Config.STOP_LOSS_PCT / 100)
        else:
            return self.entry_price * (1 + Config.STOP_LOSS_PCT / 100)

    def _calc_tp_price(self) -> float:
        """익절가 계산."""
        if self.side == "Buy":
            return self.entry_price * (1 + Config.TAKE_PROFIT_PCT / 100)
        else:
            return self.entry_price * (1 - Config.TAKE_PROFIT_PCT / 100)

    def _reset(self):
        """포지션 상태 초기화."""
        self.trade_id = None
        self.entry_time = None
        self.entry_price = 0.0
        self.side = ""
        self.qty = 0.0
        self.signals_at_entry = {}
        self.indicators_at_entry = {}
        self.trailing_active = False
        self.trailing_high = 0.0
