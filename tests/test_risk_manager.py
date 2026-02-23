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
