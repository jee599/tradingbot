"""포지션 관리 모듈 - 진입, 청산, 트레일링 스탑 (멀티심볼)."""

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
    """포지션 관리 (진입/청산/트레일링) - 심볼별 인스턴스."""

    def __init__(self, exchange: BybitExchange, risk_mgr: RiskManager,
                 bot_logger: BotLogger, notifier: TelegramNotifier,
                 symbol: str = None):
        self.exchange = exchange
        self.risk_mgr = risk_mgr
        self.bot_logger = bot_logger
        self.notifier = notifier
        self.symbol = symbol or Config.SYMBOL

        # 심볼별 정밀도
        info = exchange.get_instrument_info(self.symbol)
        self.qty_step = info["qty_step"]
        self.min_qty = info["min_qty"]
        self.tick_size = info["tick_size"]

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
        self.trailing_high: float = 0.0

        # 수수료 추적
        self.fee_rate: float = 0.00055  # Bybit taker fee 0.055%

    def _short_name(self) -> str:
        """심볼 약칭 (XRPUSDT → XRP)."""
        return self.symbol.replace("USDT", "")

    def open_position(self, side: str, margin_usdt: float, current_price: float,
                      signals: dict, indicators: dict,
                      qty_override: float | None = None) -> bool:
        """포지션 진입.

        Args:
            qty_override: 잔고 기반 사이징으로 미리 계산된 수량.
                          지정하면 margin_usdt/price 기반 계산을 건너뛴다.
        """
        if qty_override is not None and qty_override > 0:
            qty = qty_override
        else:
            position_value = margin_usdt * Config.LEVERAGE
            qty = round_qty(position_value / current_price, self.qty_step)

        if qty < self.min_qty:
            logger.error(f"POSITION [{self.symbol}]: 수량 {qty} < 최소 {self.min_qty}, 진입 불가")
            return False

        result = self.exchange.place_order(side, qty, symbol=self.symbol)
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
        name = self._short_name()

        logger.info(
            f"POSITION_OPEN [{self.symbol}]: {direction} {qty} @ ${current_price:.4f} | "
            f"마진: ${margin_usdt:.2f} | SL: ${sl_price:.4f} | TP: ${tp_price:.4f}"
        )

        confidence = signals.get("confidence", 0)
        signal_values = {
            k: v.get("value", 0) if isinstance(v, dict) else v
            for k, v in signals.items()
            if k in ("MA", "RSI", "BB", "MTF")
        }

        # 서버사이드 SL/TP 설정
        self.exchange.set_trading_stop(sl_price, tp_price, symbol=self.symbol, side=side)

        self.notifier.notify_entry(
            side=side, price=current_price, qty=qty, leverage=Config.LEVERAGE,
            sl=sl_price, tp=tp_price,
            sl_pct=Config.STOP_LOSS_PCT, tp_pct=Config.TAKE_PROFIT_PCT,
            signals=signal_values, confidence=confidence,
            symbol_name=name,
        )

        return True

    def check_exit(self, current_price: float, combined_signal: int,
                   current_indicators: dict) -> str | None:
        """청산 조건 확인."""
        if not self.side:
            return None

        pnl = pct_change(self.entry_price, current_price, self.side)

        if pnl <= -Config.STOP_LOSS_PCT:
            return "SL_HIT"

        if pnl >= Config.TAKE_PROFIT_PCT:
            return "TP_HIT"

        if pnl >= Config.TRAILING_STOP_ACTIVATE_PCT:
            if not self.trailing_active:
                self.trailing_active = True
                self.trailing_high = current_price
                logger.info(f"TRAILING [{self.symbol}]: 활성화 (PnL: +{pnl:.2f}%)")

        if self.trailing_active:
            updated = False
            if self.side == "Buy":
                if current_price > self.trailing_high:
                    self.trailing_high = current_price
                    updated = True
                drawdown = pct_change(self.trailing_high, current_price, "Buy")
            else:
                if current_price < self.trailing_high:
                    self.trailing_high = current_price
                    updated = True
                drawdown = pct_change(self.trailing_high, current_price, "Sell")

            if updated:
                if self.side == "Buy":
                    new_sl = self.trailing_high * (1 - Config.TRAILING_STOP_CALLBACK_PCT / 100)
                else:
                    new_sl = self.trailing_high * (1 + Config.TRAILING_STOP_CALLBACK_PCT / 100)
                self.exchange.update_stop_loss(new_sl, symbol=self.symbol, side=self.side)

            if drawdown <= -Config.TRAILING_STOP_CALLBACK_PCT:
                return "TRAILING_STOP"

        if self.side == "Buy" and combined_signal == -1:
            return "SIGNAL_REVERSE"
        if self.side == "Sell" and combined_signal == 1:
            return "SIGNAL_REVERSE"

        if self.entry_time:
            hours_held = (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600
            if hours_held >= Config.TIME_EXIT_HOURS and pnl < 0:
                return "TIME_EXIT"

        return None

    def close_position(self, current_price: float, exit_reason: str,
                       current_indicators: dict) -> dict | None:
        """포지션 청산 실행."""
        if not self.side:
            return None

        result = self.exchange.close_position(self.side, self.qty, symbol=self.symbol)
        if result is None:
            logger.error(f"POSITION [{self.symbol}]: 청산 주문 실패")
            return None

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
        name = self._short_name()

        trade_data = {
            "trade_id": self.trade_id,
            "timestamp_open": self.entry_time.isoformat() if self.entry_time else "",
            "timestamp_close": timestamp_now(),
            "symbol": self.symbol,
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

        self.bot_logger.log_trade(trade_data)
        self.risk_mgr.record_trade(net_pnl_pct, exit_reason)

        logger.info(
            f"POSITION_CLOSE [{self.symbol}]: {direction} | {exit_reason} | "
            f"PnL: {pnl_pct:+.2f}% | Net: ${net_pnl_usdt:+.2f} | 보유: {holding_hours:.1f}h"
        )

        self.notifier.notify_exit(
            exit_reason=exit_reason, pnl_pct=pnl_pct,
            net_pnl=net_pnl_usdt, fee_total=fee_total,
            holding_hours=holding_hours,
            symbol_name=name,
        )

        self._reset()
        return trade_data

    def sync_with_exchange(self):
        """거래소 포지션과 내부 상태 동기화."""
        pos = self.exchange.get_position(symbol=self.symbol)
        if pos is None:
            if self.side:
                logger.warning(f"POSITION [{self.symbol}]: 거래소에 포지션 없음 (서버사이드 SL/TP 실행 가능)")
                ticker = self.exchange.get_ticker(symbol=self.symbol)
                current_price = ticker.get("last_price", self.entry_price)
                pnl_pct = pct_change(self.entry_price, current_price, self.side)

                if pnl_pct <= -Config.STOP_LOSS_PCT * 0.5:
                    exit_reason = "SERVER_SL"
                elif pnl_pct >= Config.TAKE_PROFIT_PCT * 0.5:
                    exit_reason = "SERVER_TP"
                else:
                    exit_reason = "SERVER_CLOSE"

                self._log_server_close(current_price, exit_reason)
                self._reset()
        else:
            if not self.side:
                logger.warning(
                    f"POSITION [{self.symbol}]: 외부 포지션 감지 - "
                    f"{pos['side']} {pos['size']} @ {pos['entry_price']}"
                )
                self.side = pos["side"]
                self.qty = pos["size"]
                self.entry_price = pos["entry_price"]
                self.entry_time = datetime.now(timezone.utc)
                self.trade_id = generate_trade_id()

    def _log_server_close(self, current_price: float, exit_reason: str):
        """서버사이드 SL/TP 실행 시 매매 기록."""
        pnl_pct = pct_change(self.entry_price, current_price, self.side)
        position_value = self.entry_price * self.qty
        pnl_usdt = position_value * (pnl_pct / 100)
        fee_total = position_value * self.fee_rate + current_price * self.qty * self.fee_rate
        net_pnl_usdt = pnl_usdt - fee_total
        net_pnl_pct = pnl_pct - (fee_total / (position_value / Config.LEVERAGE)) * 100
        holding_hours = (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600 if self.entry_time else 0
        direction = "Long" if self.side == "Buy" else "Short"
        name = self._short_name()

        trade_data = {
            "trade_id": self.trade_id,
            "timestamp_open": self.entry_time.isoformat() if self.entry_time else "",
            "timestamp_close": timestamp_now(),
            "symbol": self.symbol,
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
            "indicators_at_exit": {},
            "risk_metrics": {
                "trailing_stop_activated": self.trailing_active,
                "trailing_stop_high": round(self.trailing_high, 6),
            },
        }
        self.bot_logger.log_trade(trade_data)
        self.risk_mgr.record_trade(net_pnl_pct, exit_reason)

        logger.info(f"SERVER_CLOSE [{self.symbol}]: {direction} | {exit_reason} | PnL: {pnl_pct:+.2f}%")
        self.notifier.notify_exit(exit_reason, pnl_pct, net_pnl_usdt, fee_total, holding_hours, symbol_name=name)

    def has_position(self) -> bool:
        return bool(self.side)

    def get_position_info(self) -> dict | None:
        if not self.side:
            return None
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "size": self.qty,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "trailing_active": self.trailing_active,
            "trailing_high": self.trailing_high,
        }

    def _calc_sl_price(self) -> float:
        if self.side == "Buy":
            return self.entry_price * (1 - Config.STOP_LOSS_PCT / 100)
        else:
            return self.entry_price * (1 + Config.STOP_LOSS_PCT / 100)

    def _calc_tp_price(self) -> float:
        if self.side == "Buy":
            return self.entry_price * (1 + Config.TAKE_PROFIT_PCT / 100)
        else:
            return self.entry_price * (1 - Config.TAKE_PROFIT_PCT / 100)

    def _reset(self):
        self.trade_id = None
        self.entry_time = None
        self.entry_price = 0.0
        self.side = ""
        self.qty = 0.0
        self.signals_at_entry = {}
        self.indicators_at_entry = {}
        self.trailing_active = False
        self.trailing_high = 0.0
