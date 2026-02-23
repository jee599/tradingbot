"""지표 계산 단위 테스트."""

import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    ema, sma, calc_rsi, calc_adx, calc_bollinger, calc_all_indicators,
)


def _make_df(n=300, base_price=2.5, seed=42):
    """테스트용 OHLCV DataFrame 생성."""
    rng = np.random.default_rng(seed)
    closes = base_price + np.cumsum(rng.normal(0, 0.005, n))
    highs = closes + rng.uniform(0.001, 0.01, n)
    lows = closes - rng.uniform(0.001, 0.01, n)
    opens = closes + rng.normal(0, 0.003, n)
    volumes = rng.uniform(500000, 2000000, n)

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


class TestEMA:
    def test_ema_length(self):
        s = pd.Series(range(100), dtype=float)
        result = ema(s, 20)
        assert len(result) == 100

    def test_ema_last_greater_for_uptrend(self):
        s = pd.Series(range(100), dtype=float)
        result = ema(s, 10)
        assert result.iloc[-1] > result.iloc[-2]

    def test_sma_matches_manual(self):
        s = pd.Series([1, 2, 3, 4, 5], dtype=float)
        result = sma(s, 3)
        assert result.iloc[-1] == pytest.approx(4.0)


class TestRSI:
    def test_rsi_range(self):
        df = _make_df(300)
        rsi = calc_rsi(df["close"], 14)
        valid = rsi.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 100

    def test_rsi_uptrend_high(self):
        # 가속 상승 추세 생성
        rng = np.random.default_rng(42)
        base = np.cumsum(np.abs(rng.normal(0.02, 0.01, 100))) + 1.0
        prices = pd.Series(base)
        rsi = calc_rsi(prices, 14)
        assert rsi.iloc[-1] >= 50  # 상승 추세이므로 50 이상

    def test_rsi_downtrend_low(self):
        prices = pd.Series(np.linspace(2, 1, 100))
        rsi = calc_rsi(prices, 14)
        assert rsi.iloc[-1] < 30


class TestADX:
    def test_adx_columns(self):
        df = _make_df(300)
        result = calc_adx(df, 14)
        assert "adx" in result.columns
        assert "plus_di" in result.columns
        assert "minus_di" in result.columns

    def test_adx_non_negative(self):
        df = _make_df(300)
        result = calc_adx(df, 14)
        valid = result.dropna()
        assert (valid["adx"] >= 0).all()


class TestBollinger:
    def test_bb_columns(self):
        df = _make_df(300)
        result = calc_bollinger(df, 20, 2.0)
        expected_cols = {"bb_upper", "bb_mid", "bb_lower", "bb_pct", "bb_width"}
        assert expected_cols == set(result.columns)

    def test_bb_upper_above_lower(self):
        df = _make_df(300)
        result = calc_bollinger(df, 20, 2.0)
        valid = result.dropna()
        assert (valid["bb_upper"] >= valid["bb_lower"]).all()

    def test_bb_pct_range(self):
        df = _make_df(300)
        result = calc_bollinger(df, 20, 2.0)
        valid = result.dropna()
        # bb_pct는 대부분 0~1 범위이지만 극단적 경우 벗어날 수 있음
        assert valid["bb_pct"].median() > 0
        assert valid["bb_pct"].median() < 1


class TestCalcAllIndicators:
    def test_all_columns_present(self):
        df = _make_df(300)
        result = calc_all_indicators(df)
        expected = [
            "ema9", "ema20", "ema50", "ema200",
            "ema20_4h", "ema50_4h",
            "rsi", "adx", "plus_di", "minus_di",
            "bb_upper", "bb_mid", "bb_lower", "bb_pct", "bb_width",
            "volume_ratio", "is_squeeze",
            "ema20_cross_up", "ema20_cross_down",
            "rsi_reversal_up", "rsi_reversal_down",
            "squeeze_release", "pullback_to_ema20",
            "is_bullish", "is_bearish",
        ]
        for col in expected:
            assert col in result.columns, f"Missing column: {col}"

    def test_no_nan_in_last_row(self):
        df = _make_df(300)
        result = calc_all_indicators(df)
        last = result.iloc[-1]
        # 핵심 지표에 NaN 없어야 함
        for col in ["ema20", "ema50", "rsi", "adx", "bb_pct"]:
            assert not pd.isna(last[col]), f"NaN in {col}"
