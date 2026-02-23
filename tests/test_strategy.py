"""전략 시그널 단위 테스트."""

import numpy as np
import pandas as pd
import pytest

from src.indicators import calc_all_indicators
from src.strategy import (
    signal_ma, signal_rsi, signal_bb, signal_mtf, generate_signals,
)


def _make_df(n=300, seed=42):
    rng = np.random.default_rng(seed)
    closes = 2.5 + np.cumsum(rng.normal(0, 0.005, n))
    highs = closes + rng.uniform(0.001, 0.01, n)
    lows = closes - rng.uniform(0.001, 0.01, n)
    opens = closes + rng.normal(0, 0.003, n)
    volumes = rng.uniform(500000, 2000000, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


class TestSignalMA:
    def test_no_signal_low_adx(self):
        row = pd.Series({"adx": 15, "ema20_cross_up": True, "ema20_above_50": True})
        val, reason = signal_ma(row)
        assert val == 0
        assert "ADX" in reason

    def test_long_on_cross_up(self):
        row = pd.Series({"adx": 25, "ema20_cross_up": True, "ema20_cross_down": False, "ema20_above_50": True})
        val, _ = signal_ma(row)
        assert val == 1

    def test_short_on_cross_down(self):
        row = pd.Series({"adx": 25, "ema20_cross_up": False, "ema20_cross_down": True, "ema20_above_50": False})
        val, _ = signal_ma(row)
        assert val == -1


class TestSignalRSI:
    def test_long_rsi_reversal(self):
        row = pd.Series({"rsi": 32, "rsi_reversal_up": True, "rsi_reversal_down": False})
        val, _ = signal_rsi(row)
        assert val == 1

    def test_short_rsi_reversal(self):
        row = pd.Series({"rsi": 70, "rsi_reversal_up": False, "rsi_reversal_down": True})
        val, _ = signal_rsi(row)
        assert val == -1

    def test_neutral_mid_range(self):
        row = pd.Series({"rsi": 50, "rsi_reversal_up": True, "rsi_reversal_down": False})
        val, _ = signal_rsi(row)
        assert val == 0


class TestSignalBB:
    def test_squeeze_release_long(self):
        row = pd.Series({
            "bb_pct": 0.6, "close": 2.5, "bb_mid": 2.45,
            "volume_ratio": 1.2, "squeeze_release": True,
        })
        val, _ = signal_bb(row)
        assert val == 1

    def test_squeeze_release_short(self):
        row = pd.Series({
            "bb_pct": 0.4, "close": 2.4, "bb_mid": 2.45,
            "volume_ratio": 1.2, "squeeze_release": True,
        })
        val, _ = signal_bb(row)
        assert val == -1

    def test_oversold_with_volume(self):
        row = pd.Series({
            "bb_pct": 0.02, "close": 2.3, "bb_mid": 2.4,
            "volume_ratio": 1.5, "squeeze_release": False,
        })
        val, _ = signal_bb(row)
        assert val == 1

    def test_no_signal_mid_band(self):
        row = pd.Series({
            "bb_pct": 0.5, "close": 2.4, "bb_mid": 2.4,
            "volume_ratio": 0.8, "squeeze_release": False,
        })
        val, _ = signal_bb(row)
        assert val == 0


class TestSignalMTF:
    def test_long_mtf(self):
        row = pd.Series({
            "ema20_4h": 2.5, "ema50_4h": 2.45,
            "pullback_to_ema20": True, "is_bullish": True,
            "is_bearish": False, "rsi": 48,
        })
        val, _ = signal_mtf(row)
        assert val == 1

    def test_short_mtf(self):
        row = pd.Series({
            "ema20_4h": 2.4, "ema50_4h": 2.45,
            "pullback_to_ema20": True, "is_bullish": False,
            "is_bearish": True, "rsi": 52,
        })
        val, _ = signal_mtf(row)
        assert val == -1

    def test_no_signal_no_pullback(self):
        row = pd.Series({
            "ema20_4h": 2.5, "ema50_4h": 2.45,
            "pullback_to_ema20": False, "is_bullish": True,
            "is_bearish": False, "rsi": 48,
        })
        val, _ = signal_mtf(row)
        assert val == 0


class TestGenerateSignals:
    def test_returns_all_keys(self):
        df = calc_all_indicators(_make_df(300))
        result = generate_signals(df)
        assert "MA" in result
        assert "RSI" in result
        assert "BB" in result
        assert "MTF" in result
        assert "combined_signal" in result
        assert "confidence" in result

    def test_insufficient_data(self):
        df = _make_df(50)
        result = generate_signals(df)
        assert result["combined_signal"] == 0

    def test_combined_signal_range(self):
        df = calc_all_indicators(_make_df(300))
        result = generate_signals(df)
        assert result["combined_signal"] in (-1, 0, 1)

    def test_confidence_matches_counts(self):
        df = calc_all_indicators(_make_df(300))
        result = generate_signals(df)
        assert result["confidence"] == max(result["buy_count"], result["sell_count"])
