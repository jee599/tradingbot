"""리스크 관리 모듈 - 하드 리밋, 쿨다운, 진입 필터."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from src.config import Config

logger = logging.getLogger("xrp_bot")


class RiskManager:
    """리스크 관리 (하드 리밋 + 진입 필터)."""

    def __init__(self, bot_logger):
        self.bot_logger = bot_logger
        self.daily_pnl: float = 0.0
        self.daily_trade_count: int = 0
        self.consecutive_sl: int = 0
        self.cooldown_until: datetime | None = None
        self.last_sl_times: list[datetime] = []
        self.daily_reset_date: str = ""
        self._check_daily_reset()

    def _check_daily_reset(self):
        """일일 카운터 리셋 (UTC 기준)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_reset_date != today:
            self.daily_pnl = 0.0
            self.daily_trade_count = 0
            self.daily_reset_date = today
            logger.info(f"RISK: 일일 카운터 리셋 ({today})")

    def record_trade(self, pnl_pct: float, exit_reason: str):
        """매매 결과 기록."""
        self._check_daily_reset()
        self.daily_pnl += pnl_pct
        self.daily_trade_count += 1

        if exit_reason == "SL_HIT":
            self.consecutive_sl += 1
            self.last_sl_times.append(datetime.now(timezone.utc))
            if self.consecutive_sl >= Config.COOLDOWN_AFTER_SL_STREAK:
                self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=Config.COOLDOWN_HOURS)
                logger.warning(
                    f"RISK: 연속 손절 {self.consecutive_sl}회 → "
                    f"{Config.COOLDOWN_HOURS}시간 쿨다운 ({self.cooldown_until}까지)"
                )
        else:
            self.consecutive_sl = 0

    def can_trade(self) -> tuple[bool, str]:
        """매매 가능 여부 확인.

        Returns:
            (가능 여부, 사유)
        """
        self._check_daily_reset()

        # 쿨다운
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
            msg = f"쿨다운 중: {remaining:.0f}분 남음 (연속 손절 {self.consecutive_sl}회)"
            logger.warning(f"RISK: {msg}")
            return False, msg
        elif self.cooldown_until:
            self.cooldown_until = None
            self.consecutive_sl = 0
            logger.info("RISK: 쿨다운 해제")

        return True, "OK"

    def check_entry_filters(self, df, has_position: bool) -> dict:
        """진입 필터 체크.

        Returns:
            {
                "recent_sl": bool,
                "low_volume": bool,
                "wide_spread": bool,
                "already_in_position": bool,
                "passed": bool,
            }
        """
        result = {
            "recent_sl": False,
            "low_volume": False,
            "wide_spread": False,
            "already_in_position": has_position,
            "passed": True,
        }

        # 최근 3봉 내 손절 발생
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=Config.RECENT_SL_LOOKBACK)
        recent_sls = [t for t in self.last_sl_times if t > recent_cutoff]
        if recent_sls:
            result["recent_sl"] = True
            result["passed"] = False

        # 거래량이 20봉 평균의 30% 미만
        if not df.empty and len(df) >= 20:
            vol_ratio = df["volume_ratio"].iloc[-1]
            if vol_ratio < Config.MIN_VOLUME_RATIO:
                result["low_volume"] = True
                result["passed"] = False

        # 이미 포지션 보유 중
        if has_position:
            result["passed"] = False

        return result

    def check_spread_filter(self, spread: float, avg_spread: float) -> bool:
        """스프레드 필터. 평소의 3배 이상이면 진입 금지."""
        if avg_spread > 0 and spread > avg_spread * Config.MAX_SPREAD_MULTIPLIER:
            logger.warning(f"RISK: 스프레드 비정상 ({spread:.6f} vs avg {avg_spread:.6f})")
            return False
        return True

    def calc_position_size(self, equity: float, confidence: int) -> float:
        """포지션 사이즈 계산 (USDT) — 레거시, 잔고 기반 사이징 권장.

        Args:
            equity: 총 자산 (USDT)
            confidence: 동의 지표 수

        Returns:
            포지션에 투입할 USDT 금액 (레버리지 적용 전 마진)
        """
        if confidence >= Config.HIGH_CONFIDENCE_THRESHOLD:
            size_pct = Config.HIGH_CONFIDENCE_SIZE_PCT
        else:
            size_pct = Config.POSITION_SIZE_PCT

        size_pct = min(size_pct, Config.MAX_POSITION_SIZE_PCT)
        margin = equity * (size_pct / 100)
        return margin

    def calc_qty_from_balance(
        self,
        available_balance: float,
        mark_price: float,
        qty_step: float,
        min_qty: float,
        leverage: int | None = None,
    ) -> tuple[float, dict]:
        """잔고 기반 포지션 수량 계산.

        ErrCode 110007(잔고 부족) 방지를 위해 가용 잔고에서
        reserve를 빼고 utilization/haircut을 적용하여 수량을 산출한다.

        Args:
            available_balance: 가용 USDT (availableToWithdraw)
            mark_price: 현재가 / 마크 가격
            qty_step: 종목의 수량 단위 (예: 0.1)
            min_qty: 종목의 최소 수량
            leverage: 레버리지 (기본 Config.LEVERAGE)

        Returns:
            (qty, detail_dict)
            qty: 주문 수량 (0이면 진입 불가)
            detail_dict: 로그용 계산 상세
        """
        from src.utils import round_qty as _round_qty

        leverage = leverage or Config.LEVERAGE
        reserve = Config.MIN_RESERVE_USDT
        utilization = Config.BALANCE_UTILIZATION_PCT / 100
        haircut = Config.SAFETY_HAIRCUT_PCT / 100

        usable = available_balance - reserve
        if usable <= 0 or mark_price <= 0:
            detail = {
                "available": round(available_balance, 4),
                "reserve": reserve,
                "usable": round(max(usable, 0), 4),
                "reason": "insufficient_balance",
            }
            logger.warning(f"SIZING: 잔고 부족 — available={available_balance:.2f}, reserve={reserve}")
            return 0.0, detail

        notional = usable * utilization * (1 - haircut)
        position_value = notional * leverage
        raw_qty = position_value / mark_price
        qty = _round_qty(raw_qty, qty_step)

        detail = {
            "available": round(available_balance, 4),
            "reserve": reserve,
            "utilization_pct": Config.BALANCE_UTILIZATION_PCT,
            "haircut_pct": Config.SAFETY_HAIRCUT_PCT,
            "usable": round(usable, 4),
            "notional": round(notional, 4),
            "leverage": leverage,
            "position_value": round(position_value, 4),
            "mark_price": round(mark_price, 6),
            "raw_qty": round(raw_qty, 6),
            "qty": qty,
            "qty_step": qty_step,
            "min_qty": min_qty,
        }

        if qty < min_qty:
            detail["reason"] = "below_min_qty"
            logger.warning(
                f"SIZING: 최소 수량 미달 — qty={qty} < min_qty={min_qty} "
                f"(available={available_balance:.2f})"
            )
            return 0.0, detail

        detail["reason"] = "ok"
        logger.info(
            f"SIZING: qty={qty} | available={available_balance:.2f} "
            f"→ usable={usable:.2f} → notional={notional:.2f} "
            f"× {leverage}x → {position_value:.2f} / price={mark_price:.4f}"
        )
        return qty, detail

    def get_status(self) -> dict:
        """리스크 관리 현황."""
        return {
            "daily_pnl": self.daily_pnl,
            "daily_trade_count": self.daily_trade_count,
            "consecutive_sl": self.consecutive_sl,
            "cooldown_until": str(self.cooldown_until) if self.cooldown_until else None,
            "can_trade": self.can_trade()[0],
        }
