"""리스크 관리 단위 테스트."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

from src.risk_manager import RiskManager
from src.config import Config


@pytest.fixture
def risk_mgr():
    mock_logger = MagicMock()
    return RiskManager(mock_logger)


class TestCanTrade:
    def test_can_trade_initially(self, risk_mgr):
        ok, reason = risk_mgr.can_trade()
        assert ok is True
        assert reason == "OK"

    def test_blocked_after_max_daily_loss(self, risk_mgr):
        risk_mgr.daily_pnl = -Config.MAX_DAILY_LOSS_PCT
        ok, reason = risk_mgr.can_trade()
        assert ok is False
        assert "일일 최대 손실" in reason

    def test_blocked_after_max_daily_trades(self, risk_mgr):
        risk_mgr.daily_trade_count = Config.MAX_DAILY_TRADES
        ok, reason = risk_mgr.can_trade()
        assert ok is False
        assert "최대 매매 횟수" in reason

    def test_cooldown_after_consecutive_sl(self, risk_mgr):
        # 작은 손실로 일일 한도 초과 방지
        for _ in range(Config.COOLDOWN_AFTER_SL_STREAK):
            risk_mgr.record_trade(-0.5, "SL_HIT")
        ok, reason = risk_mgr.can_trade()
        assert ok is False
        assert "쿨다운" in reason

    def test_cooldown_expires(self, risk_mgr):
        for _ in range(Config.COOLDOWN_AFTER_SL_STREAK):
            risk_mgr.record_trade(-0.5, "SL_HIT")
        # 쿨다운 시간을 과거로 설정 + 일일 PnL 리셋
        risk_mgr.cooldown_until = datetime.now(timezone.utc) - timedelta(hours=1)
        risk_mgr.daily_pnl = 0.0  # 일일 손실 한도 리셋
        ok, reason = risk_mgr.can_trade()
        assert ok is True


class TestRecordTrade:
    def test_consecutive_sl_count(self, risk_mgr):
        risk_mgr.record_trade(-2.0, "SL_HIT")
        assert risk_mgr.consecutive_sl == 1
        risk_mgr.record_trade(-1.5, "SL_HIT")
        assert risk_mgr.consecutive_sl == 2

    def test_sl_streak_resets_on_win(self, risk_mgr):
        risk_mgr.record_trade(-2.0, "SL_HIT")
        risk_mgr.record_trade(-2.0, "SL_HIT")
        risk_mgr.record_trade(4.0, "TP_HIT")
        assert risk_mgr.consecutive_sl == 0

    def test_daily_pnl_accumulates(self, risk_mgr):
        risk_mgr.record_trade(2.0, "TP_HIT")
        risk_mgr.record_trade(-1.0, "SL_HIT")
        assert risk_mgr.daily_pnl == pytest.approx(1.0)

    def test_daily_trade_count(self, risk_mgr):
        risk_mgr.record_trade(1.0, "TP_HIT")
        risk_mgr.record_trade(-1.0, "SL_HIT")
        assert risk_mgr.daily_trade_count == 2


class TestPositionSizing:
    def test_normal_size(self, risk_mgr):
        margin = risk_mgr.calc_position_size(10000, confidence=2)
        expected = 10000 * Config.POSITION_SIZE_PCT / 100
        assert margin == pytest.approx(expected)

    def test_high_confidence_size(self, risk_mgr):
        margin = risk_mgr.calc_position_size(10000, confidence=3)
        expected = 10000 * Config.HIGH_CONFIDENCE_SIZE_PCT / 100
        assert margin == pytest.approx(expected)

    def test_max_cap(self, risk_mgr):
        # MAX_POSITION_SIZE_PCT가 10이므로 초과하지 않아야 함
        margin = risk_mgr.calc_position_size(10000, confidence=4)
        max_margin = 10000 * Config.MAX_POSITION_SIZE_PCT / 100
        assert margin <= max_margin


class TestCalcQtyFromBalance:
    """잔고 기반 포지션 수량 계산 테스트."""

    def test_normal_sizing(self, risk_mgr):
        """정상 잔고에서 수량 산출."""
        # available=100, reserve=10, usable=90
        # notional = 90 * 0.90 * 0.95 = 76.95
        # position_value = 76.95 * 1 (leverage) = 76.95
        # qty = 76.95 / 2.0 = 38.475 → round down to step 0.1 → 38.4
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=100.0,
            mark_price=2.0,
            qty_step=0.1,
            min_qty=1.0,
            leverage=1,
        )
        assert qty == pytest.approx(38.4)
        assert detail["reason"] == "ok"

    def test_with_leverage(self, risk_mgr):
        """레버리지 적용 확인."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=100.0,
            mark_price=2.0,
            qty_step=0.1,
            min_qty=1.0,
            leverage=3,
        )
        # notional = 90 * 0.90 * 0.95 = 76.95
        # position_value = 76.95 * 3 = 230.85
        # qty = 230.85 / 2.0 = 115.425 → 115.4
        assert qty == pytest.approx(115.4)
        assert detail["leverage"] == 3

    def test_qty_step_rounding(self, risk_mgr):
        """수량이 qty_step 단위로 내림 처리."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=50.0,
            mark_price=3.0,
            qty_step=1.0,
            min_qty=1.0,
            leverage=1,
        )
        # usable=40, notional=40*0.9*0.95=34.2
        # qty=34.2/3.0=11.4 → step=1 → 11.0
        assert qty == pytest.approx(11.0)

    def test_insufficient_balance(self, risk_mgr):
        """잔고가 reserve 이하이면 qty=0."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=5.0,   # < reserve (10)
            mark_price=2.0,
            qty_step=0.1,
            min_qty=1.0,
        )
        assert qty == 0.0
        assert detail["reason"] == "insufficient_balance"

    def test_zero_price(self, risk_mgr):
        """가격이 0이면 qty=0."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=100.0,
            mark_price=0.0,
            qty_step=0.1,
            min_qty=1.0,
        )
        assert qty == 0.0
        assert detail["reason"] == "insufficient_balance"

    def test_below_min_qty(self, risk_mgr):
        """계산된 수량이 min_qty 미만이면 qty=0."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=11.0,   # usable=1
            mark_price=2.0,
            qty_step=1.0,
            min_qty=5.0,             # min_qty=5 > calculated qty
            leverage=1,
        )
        # usable=1, notional=1*0.9*0.95=0.855, qty=0.855/2=0.4275 → 0 (step=1)
        assert qty == 0.0
        assert detail["reason"] == "below_min_qty"

    def test_exact_reserve_boundary(self, risk_mgr):
        """잔고가 정확히 reserve와 같으면 qty=0."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=10.0,   # == reserve
            mark_price=2.0,
            qty_step=0.1,
            min_qty=0.1,
        )
        assert qty == 0.0
        assert detail["reason"] == "insufficient_balance"

    def test_detail_dict_fields(self, risk_mgr):
        """반환 dict에 로그용 필드들이 포함되는지 확인."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=200.0,
            mark_price=2.5,
            qty_step=0.1,
            min_qty=1.0,
            leverage=2,
        )
        assert qty > 0
        for key in ("available", "reserve", "utilization_pct", "haircut_pct",
                     "usable", "notional", "leverage", "position_value",
                     "mark_price", "raw_qty", "qty", "qty_step", "min_qty", "reason"):
            assert key in detail, f"Missing key: {key}"

    def test_large_qty_step(self, risk_mgr):
        """qty_step이 크면 많이 내림될 수 있음."""
        qty, detail = risk_mgr.calc_qty_from_balance(
            available_balance=100.0,
            mark_price=2.0,
            qty_step=10.0,
            min_qty=10.0,
            leverage=1,
        )
        # notional=76.95, qty=76.95/2=38.475 → step=10 → 30.0
        assert qty == pytest.approx(30.0)
        assert detail["reason"] == "ok"


class TestEntryFilters:
    def test_no_position_passes(self, risk_mgr):
        import pandas as pd
        import numpy as np
        df = pd.DataFrame({"volume_ratio": np.ones(30)})
        result = risk_mgr.check_entry_filters(df, has_position=False)
        assert result["passed"] is True

    def test_already_in_position_fails(self, risk_mgr):
        import pandas as pd
        import numpy as np
        df = pd.DataFrame({"volume_ratio": np.ones(30)})
        result = risk_mgr.check_entry_filters(df, has_position=True)
        assert result["passed"] is False
        assert result["already_in_position"] is True

    def test_low_volume_fails(self, risk_mgr):
        import pandas as pd
        import numpy as np
        df = pd.DataFrame({"volume_ratio": np.full(30, 0.1)})
        result = risk_mgr.check_entry_filters(df, has_position=False)
        assert result["low_volume"] is True
        assert result["passed"] is False

    def test_recent_sl_fails(self, risk_mgr):
        import pandas as pd
        import numpy as np
        risk_mgr.last_sl_times.append(datetime.now(timezone.utc))
        df = pd.DataFrame({"volume_ratio": np.ones(30)})
        result = risk_mgr.check_entry_filters(df, has_position=False)
        assert result["recent_sl"] is True
        assert result["passed"] is False
