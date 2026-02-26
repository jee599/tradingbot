"""스캘핑 전략 시그널 단위 테스트."""

import numpy as np
import pandas as pd
import pytest

from src.strategy_scalp import (
    calc_trend_filter,
    calc_scalp_indicators,
    signal_pullback,
    signal_breakout,
    generate_scalp_signals,
)


def _make_df(n=300, seed=42, trend="up"):
    """테스트용 OHLCV DataFrame 생성.

    trend:
      "up"   → 강한 상승 (EMA50 > EMA200 보장)
      "down" → 강한 하락 (EMA50 < EMA200 보장)
      "flat" → 횡보
    """
    rng = np.random.default_rng(seed)
    if trend == "up":
        # Strong monotonic uptrend with small noise
        closes = 100.0 + np.arange(n) * 0.1 + rng.normal(0, 0.02, n)
    elif trend == "down":
        closes = 200.0 - np.arange(n) * 0.1 + rng.normal(0, 0.02, n)
    else:
        closes = 100.0 + rng.normal(0, 0.02, n)
    closes = np.maximum(closes, 1.0)
    highs = closes + rng.uniform(0.01, 0.2, n)
    lows = closes - rng.uniform(0.01, 0.2, n)
    opens = closes + rng.normal(0, 0.05, n)
    volumes = rng.uniform(500000, 2000000, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


# ──────────────────────────────────────────
# 15m 추세 필터 테스트
# ──────────────────────────────────────────

class TestTrendFilter:
    def test_uptrend_returns_positive(self):
        df = _make_df(300, trend="up")
        result = calc_trend_filter(df)
        assert result == 1

    def test_downtrend_returns_negative(self):
        df = _make_df(300, trend="down")
        result = calc_trend_filter(df)
        assert result == -1

    def test_insufficient_data_returns_zero(self):
        df = _make_df(50, trend="up")
        result = calc_trend_filter(df)
        assert result == 0

    def test_empty_df_returns_zero(self):
        df = pd.DataFrame()
        result = calc_trend_filter(df)
        assert result == 0


# ──────────────────────────────────────────
# 5m 지표 계산 테스트
# ──────────────────────────────────────────

class TestScalpIndicators:
    def test_all_columns_present(self):
        df = _make_df(100, trend="up")
        result = calc_scalp_indicators(df)
        expected_cols = [
            "ema20", "rsi", "bb_upper", "bb_mid", "bb_lower",
            "bb_pct", "volume_ratio", "is_bullish", "is_bearish",
            "pullback_to_ema20", "bb_breakout_up", "bb_breakout_down",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_rsi_in_range(self):
        df = _make_df(100, trend="up")
        result = calc_scalp_indicators(df)
        assert result["rsi"].iloc[-1] >= 0
        assert result["rsi"].iloc[-1] <= 100

    def test_no_nan_in_last_row(self):
        df = _make_df(100, trend="up")
        result = calc_scalp_indicators(df)
        last = result.iloc[-1]
        for col in ["ema20", "rsi", "bb_pct", "volume_ratio"]:
            assert not pd.isna(last[col]), f"NaN in last row: {col}"


# ──────────────────────────────────────────
# Pullback 트리거 테스트
# ──────────────────────────────────────────

class TestPullbackSignal:
    def test_long_pullback(self):
        row = pd.Series({
            "pullback_to_ema20": True,
            "is_bullish": True,
            "is_bearish": False,
            "rsi": 50,
        })
        val, reason = signal_pullback(row, trend=1)
        assert val == 1
        assert "Pullback long" in reason

    def test_short_pullback(self):
        row = pd.Series({
            "pullback_to_ema20": True,
            "is_bullish": False,
            "is_bearish": True,
            "rsi": 50,
        })
        val, reason = signal_pullback(row, trend=-1)
        assert val == -1
        assert "Pullback short" in reason

    def test_no_pullback_when_no_trend(self):
        row = pd.Series({
            "pullback_to_ema20": True,
            "is_bullish": True,
            "is_bearish": False,
            "rsi": 50,
        })
        val, _ = signal_pullback(row, trend=0)
        assert val == 0

    def test_no_pullback_when_not_near_ema(self):
        row = pd.Series({
            "pullback_to_ema20": False,
            "is_bullish": True,
            "is_bearish": False,
            "rsi": 50,
        })
        val, _ = signal_pullback(row, trend=1)
        assert val == 0

    def test_no_pullback_wrong_candle_direction(self):
        """Trend is long but candle is bearish → no pullback."""
        row = pd.Series({
            "pullback_to_ema20": True,
            "is_bullish": False,
            "is_bearish": True,
            "rsi": 50,
        })
        val, _ = signal_pullback(row, trend=1)
        assert val == 0

    def test_no_pullback_rsi_out_of_range(self):
        """RSI outside acceptable band → no pullback."""
        row = pd.Series({
            "pullback_to_ema20": True,
            "is_bullish": True,
            "is_bearish": False,
            "rsi": 80,  # Too high for long pullback
        })
        val, _ = signal_pullback(row, trend=1)
        assert val == 0


# ──────────────────────────────────────────
# Breakout 트리거 테스트
# ──────────────────────────────────────────

class TestBreakoutSignal:
    def test_long_breakout(self):
        row = pd.Series({
            "bb_breakout_up": True,
            "bb_breakout_down": False,
            "volume_ratio": 2.0,
        })
        val, reason = signal_breakout(row, trend=1)
        assert val == 1
        assert "BB breakout long" in reason

    def test_short_breakout(self):
        row = pd.Series({
            "bb_breakout_up": False,
            "bb_breakout_down": True,
            "volume_ratio": 2.0,
        })
        val, reason = signal_breakout(row, trend=-1)
        assert val == -1
        assert "BB breakout short" in reason

    def test_no_breakout_when_no_trend(self):
        row = pd.Series({
            "bb_breakout_up": True,
            "bb_breakout_down": False,
            "volume_ratio": 2.0,
        })
        val, _ = signal_breakout(row, trend=0)
        assert val == 0

    def test_no_breakout_low_volume(self):
        """BB breakout but volume too low → no signal."""
        row = pd.Series({
            "bb_breakout_up": True,
            "bb_breakout_down": False,
            "volume_ratio": 0.8,  # below threshold
        })
        val, _ = signal_breakout(row, trend=1)
        assert val == 0

    def test_no_breakout_wrong_direction(self):
        """Trend is long but breakout is down → no signal."""
        row = pd.Series({
            "bb_breakout_up": False,
            "bb_breakout_down": True,
            "volume_ratio": 2.0,
        })
        val, _ = signal_breakout(row, trend=1)
        assert val == 0


# ──────────────────────────────────────────
# 통합 시그널 테스트
# ──────────────────────────────────────────

class TestGenerateScalpSignals:
    def test_returns_all_keys(self):
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        expected_keys = [
            "trend_filter", "trend_reason",
            "pullback", "breakout",
            "combined_signal", "signal_detail",
            "confidence", "trigger",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_combined_signal_range(self):
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        assert result["combined_signal"] in (-1, 0, 1)

    def test_confidence_range(self):
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        assert 0 <= result["confidence"] <= 2

    def test_insufficient_5m_data(self):
        df_5m = _make_df(10, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        assert result["combined_signal"] == 0

    def test_trend_filter_propagated(self):
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        assert result["trend_filter"] == 1

    def test_no_signal_when_trend_neutral(self):
        """No 15m trend → always 0 combined signal."""
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(50, trend="up")  # Too short for filter
        result = generate_scalp_signals(df_5m, df_15m)
        assert result["combined_signal"] == 0

    def test_trigger_field_values(self):
        df_5m = _make_df(100, trend="up")
        df_15m = _make_df(300, trend="up")
        result = generate_scalp_signals(df_5m, df_15m)
        assert result["trigger"] in ("pullback", "breakout", "both", "none")
