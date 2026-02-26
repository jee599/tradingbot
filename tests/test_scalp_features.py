"""스캘핑 개선 기능 테스트 - MFE/MAE, fee buffer, spread filter, time exit."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.config import Config
from src.risk_manager import RiskManager
from src.utils import pct_change


# ──────────────────────────────────────────
# Fee buffer SL/TP 테스트
# ──────────────────────────────────────────

class TestFeeBufferSLTP:
    """SCALP_FEE_BUFFER_PCT가 SL/TP 가격에 반영되는지 테스트."""

    def _make_mock_pos_mgr(self, side, entry_price):
        """Create a minimal PositionManager-like mock for SL/TP testing."""
        from src.position import PositionManager

        with patch.object(PositionManager, '__init__', lambda self, *a, **k: None):
            mgr = PositionManager.__new__(PositionManager)
            mgr.side = side
            mgr.entry_price = entry_price
            mgr.symbol = "XRPUSDT"
            mgr.qty_step = 0.1
            mgr.min_qty = 1.0
            mgr.tick_size = 0.0001
            return mgr

    def test_scalp_sl_widened_by_buffer(self):
        """Scalp mode: SL is widened by fee buffer."""
        orig_scalp = Config.SCALP_MODE
        orig_sl = Config.SCALP_STOP_LOSS_PCT
        orig_buf = Config.SCALP_FEE_BUFFER_PCT
        try:
            Config.SCALP_MODE = True
            Config.SCALP_STOP_LOSS_PCT = 0.8
            Config.SCALP_FEE_BUFFER_PCT = 0.15

            mgr = self._make_mock_pos_mgr("Buy", 100.0)
            sl = mgr._calc_sl_price()
            # SL% = 0.8 + 0.15 = 0.95%
            expected = 100.0 * (1 - 0.95 / 100)
            assert sl == pytest.approx(expected, rel=1e-6)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.SCALP_STOP_LOSS_PCT = orig_sl
            Config.SCALP_FEE_BUFFER_PCT = orig_buf

    def test_scalp_tp_narrowed_by_buffer(self):
        """Scalp mode: TP is narrowed by fee buffer."""
        orig_scalp = Config.SCALP_MODE
        orig_tp = Config.SCALP_TAKE_PROFIT_PCT
        orig_buf = Config.SCALP_FEE_BUFFER_PCT
        try:
            Config.SCALP_MODE = True
            Config.SCALP_TAKE_PROFIT_PCT = 1.4
            Config.SCALP_FEE_BUFFER_PCT = 0.15

            mgr = self._make_mock_pos_mgr("Buy", 100.0)
            tp = mgr._calc_tp_price()
            # TP% = 1.4 - 0.15 = 1.25%
            expected = 100.0 * (1 + 1.25 / 100)
            assert tp == pytest.approx(expected, rel=1e-6)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.SCALP_TAKE_PROFIT_PCT = orig_tp
            Config.SCALP_FEE_BUFFER_PCT = orig_buf

    def test_scalp_short_sl_widened(self):
        """Short side: SL is widened upward."""
        orig_scalp = Config.SCALP_MODE
        orig_sl = Config.SCALP_STOP_LOSS_PCT
        orig_buf = Config.SCALP_FEE_BUFFER_PCT
        try:
            Config.SCALP_MODE = True
            Config.SCALP_STOP_LOSS_PCT = 0.8
            Config.SCALP_FEE_BUFFER_PCT = 0.15

            mgr = self._make_mock_pos_mgr("Sell", 100.0)
            sl = mgr._calc_sl_price()
            expected = 100.0 * (1 + 0.95 / 100)
            assert sl == pytest.approx(expected, rel=1e-6)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.SCALP_STOP_LOSS_PCT = orig_sl
            Config.SCALP_FEE_BUFFER_PCT = orig_buf

    def test_legacy_mode_no_buffer(self):
        """Legacy mode: SL/TP use original values without buffer."""
        orig_scalp = Config.SCALP_MODE
        orig_sl = Config.STOP_LOSS_PCT
        try:
            Config.SCALP_MODE = False
            Config.STOP_LOSS_PCT = 2.0

            mgr = self._make_mock_pos_mgr("Buy", 100.0)
            sl = mgr._calc_sl_price()
            expected = 100.0 * (1 - 2.0 / 100)
            assert sl == pytest.approx(expected, rel=1e-6)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.STOP_LOSS_PCT = orig_sl

    def test_tp_buffer_floor(self):
        """TP can't go below 0.1% even with large buffer."""
        orig_scalp = Config.SCALP_MODE
        orig_tp = Config.SCALP_TAKE_PROFIT_PCT
        orig_buf = Config.SCALP_FEE_BUFFER_PCT
        try:
            Config.SCALP_MODE = True
            Config.SCALP_TAKE_PROFIT_PCT = 0.2
            Config.SCALP_FEE_BUFFER_PCT = 0.5  # buffer > TP

            mgr = self._make_mock_pos_mgr("Buy", 100.0)
            tp = mgr._calc_tp_price()
            # TP% = max(0.2 - 0.5, 0.1) = 0.1%
            expected = 100.0 * (1 + 0.1 / 100)
            assert tp == pytest.approx(expected, rel=1e-6)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.SCALP_TAKE_PROFIT_PCT = orig_tp
            Config.SCALP_FEE_BUFFER_PCT = orig_buf


# ──────────────────────────────────────────
# MFE/MAE 테스트
# ──────────────────────────────────────────

class TestMFEMAE:
    """MFE/MAE/R-multiple tracking tests."""

    def _make_mock_pos_mgr(self, side, entry_price):
        from src.position import PositionManager

        with patch.object(PositionManager, '__init__', lambda self, *a, **k: None):
            mgr = PositionManager.__new__(PositionManager)
            mgr.side = side
            mgr.entry_price = entry_price
            mgr.symbol = "XRPUSDT"
            mgr.running_high_price = entry_price
            mgr.running_low_price = entry_price
            return mgr

    def test_update_price_extremes_long(self):
        """Long: track running high/low correctly."""
        mgr = self._make_mock_pos_mgr("Buy", 100.0)
        mgr.update_price_extremes(102.0)
        assert mgr.running_high_price == 102.0
        assert mgr.running_low_price == 100.0

        mgr.update_price_extremes(98.0)
        assert mgr.running_high_price == 102.0
        assert mgr.running_low_price == 98.0

    def test_update_price_extremes_short(self):
        """Short: same extremes tracked."""
        mgr = self._make_mock_pos_mgr("Sell", 100.0)
        mgr.update_price_extremes(98.0)
        assert mgr.running_low_price == 98.0

        mgr.update_price_extremes(103.0)
        assert mgr.running_high_price == 103.0
        assert mgr.running_low_price == 98.0

    def test_mfe_mae_long_win(self):
        """Long trade that wins: MFE > 0, MAE <= 0."""
        orig_scalp = Config.SCALP_MODE
        orig_sl = Config.SCALP_STOP_LOSS_PCT
        try:
            Config.SCALP_MODE = True
            Config.SCALP_STOP_LOSS_PCT = 0.8
            mgr = self._make_mock_pos_mgr("Buy", 100.0)
            mgr.running_high_price = 102.0  # best was +2%
            mgr.running_low_price = 99.5    # worst was -0.5%

            result = mgr.calc_mfe_mae(101.5)  # exit at +1.5%
            assert result["mfe_pct"] == pytest.approx(2.0, rel=1e-3)
            assert result["mae_pct"] == pytest.approx(-0.5, rel=1e-3)
            assert result["r_multiple"] == pytest.approx(1.5 / 0.8, rel=1e-2)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.SCALP_STOP_LOSS_PCT = orig_sl

    def test_mfe_mae_short_win(self):
        """Short trade that wins: MFE > 0 (price went down)."""
        orig_scalp = Config.SCALP_MODE
        orig_sl = Config.STOP_LOSS_PCT
        try:
            Config.SCALP_MODE = False
            Config.STOP_LOSS_PCT = 2.0
            mgr = self._make_mock_pos_mgr("Sell", 100.0)
            mgr.running_high_price = 100.5  # adverse +0.5%
            mgr.running_low_price = 97.0    # favorable: +3%

            result = mgr.calc_mfe_mae(98.0)  # exit at +2%
            assert result["mfe_pct"] == pytest.approx(3.0, rel=1e-3)
            assert result["mae_pct"] == pytest.approx(-0.5, rel=1e-3)
            assert result["r_multiple"] == pytest.approx(2.0 / 2.0, rel=1e-2)
        finally:
            Config.SCALP_MODE = orig_scalp
            Config.STOP_LOSS_PCT = orig_sl

    def test_mfe_mae_no_position(self):
        """No position → zeros."""
        mgr = self._make_mock_pos_mgr("", 0.0)
        result = mgr.calc_mfe_mae(100.0)
        assert result["mfe_pct"] == 0
        assert result["mae_pct"] == 0
        assert result["r_multiple"] == 0

    def test_update_extremes_no_position_noop(self):
        """No position → update_price_extremes is a no-op."""
        mgr = self._make_mock_pos_mgr("", 0.0)
        mgr.running_high_price = 0.0
        mgr.running_low_price = float("inf")
        mgr.update_price_extremes(50.0)
        assert mgr.running_high_price == 0.0  # unchanged


# ──────────────────────────────────────────
# Spread filter 테스트
# ──────────────────────────────────────────

class TestSpreadFilter:
    """Spread filter integration tests."""

    @pytest.fixture
    def risk_mgr(self):
        mock_logger = MagicMock()
        return RiskManager(mock_logger)

    def test_normal_spread_passes(self, risk_mgr):
        assert risk_mgr.check_spread_filter(0.001, 0.001) is True

    def test_wide_spread_blocked(self, risk_mgr):
        # spread = 0.004, avg = 0.001 → 4x > 3x threshold
        assert risk_mgr.check_spread_filter(0.004, 0.001) is False

    def test_zero_avg_spread_passes(self, risk_mgr):
        """avg_spread=0 → always pass (no data yet)."""
        assert risk_mgr.check_spread_filter(0.01, 0.0) is True

    def test_exactly_at_threshold(self, risk_mgr):
        """Spread exactly at 3x → passes (> not >=)."""
        assert risk_mgr.check_spread_filter(0.003, 0.001) is True


# ──────────────────────────────────────────
# Config 새 파라미터 테스트
# ──────────────────────────────────────────

class TestScalpConfig:
    """New scalp config parameters exist and have sensible defaults."""

    def test_regime_filter_defaults(self):
        assert hasattr(Config, "SCALP_REGIME_FILTER")
        assert hasattr(Config, "SCALP_REGIME_ADX_MIN")
        assert hasattr(Config, "SCALP_REGIME_BB_WIDTH_MIN")
        assert Config.SCALP_REGIME_ADX_MIN == 20
        assert Config.SCALP_REGIME_BB_WIDTH_MIN == 0.005

    def test_fee_buffer_default(self):
        assert hasattr(Config, "SCALP_FEE_BUFFER_PCT")
        assert Config.SCALP_FEE_BUFFER_PCT == pytest.approx(0.15)

    def test_breakeven_time_exit_default(self):
        assert hasattr(Config, "SCALP_TIME_EXIT_BREAKEVEN_MIN")
        assert Config.SCALP_TIME_EXIT_BREAKEVEN_MIN == 30
