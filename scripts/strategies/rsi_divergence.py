"""RSI Divergence Scalp Strategy (v6 - Final)

Core concept: Detect STRONG RSI divergences on 5m (from deep extreme zones)
and require at least one momentum confirmation. Use reasonable TP/SL that
matches the actual edge of divergence reversals.

Entry requirements:
  1. RSI divergence from extreme zones (RSI < 30 for bullish, > 70 for bearish)
  2. Divergence magnitude >= 5 RSI points
  3. Current RSI still showing reversal potential (< 45 bull, > 55 bear)
  4. Candle body confirmation (bullish/bearish)
  5. 15m trend alignment (not counter-trend)
  6. At least one momentum confirmation (MACD hist turning OR StochRSI cross)
  7. Volume >= 0.8x average

TP/SL calibrated to the actual MFE/MAE distribution observed in testing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "RSI Divergence Scalp"

BT_CONFIG = ScalpBacktestConfig(
    stop_loss_pct=0.8,
    take_profit_pct=1.5,
    trailing_activate_pct=0.9,
    trailing_callback_pct=0.4,
    time_exit_bars=12,       # 12 * 5min = 60min
    cooldown_bars=5,
    fee_buffer_pct=0.15,
)


# ─── Divergence detection ────────────────────────────────


def _find_swing_lows(prices: np.ndarray, window: int = 4):
    """Find indices where price is a local minimum within +/- window bars."""
    lows = []
    n = len(prices)
    for i in range(window, n - window):
        seg = prices[i - window: i + window + 1]
        if prices[i] <= seg.min() + 1e-10:
            lows.append(i)
    return lows


def _find_swing_highs(prices: np.ndarray, window: int = 4):
    """Find indices where price is a local maximum within +/- window bars."""
    highs = []
    n = len(prices)
    for i in range(window, n - window):
        seg = prices[i - window: i + window + 1]
        if prices[i] >= seg.max() - 1e-10:
            highs.append(i)
    return highs


def detect_bullish_divergence(
    close: np.ndarray,
    rsi: np.ndarray,
) -> tuple[bool, str, float]:
    """Detect bullish RSI divergence (price lower low, RSI higher low).

    Requirements:
      - Previous swing low had RSI < 30
      - Current price at/below that level (within 0.2%)
      - Current RSI at least 5 points higher
      - Current RSI < 45
      - Lookback range: 10-40 bars

    Returns (detected, reason, rsi_divergence_magnitude).
    """
    n = len(close)
    if n < 55:
        return False, "", 0.0

    cur_price = close[-1]
    cur_rsi = rsi[-1]

    if cur_rsi > 45:
        return False, "", 0.0

    start = max(0, n - 45)
    end = n - 3

    swing_indices = _find_swing_lows(close[start:end])
    if not swing_indices:
        return False, "", 0.0

    best_div = 0.0
    best_reason = ""

    for local_idx in swing_indices:
        abs_idx = start + local_idx
        bars_ago = n - 1 - abs_idx
        if bars_ago < 10 or bars_ago > 40:
            continue

        prev_price = close[abs_idx]
        prev_rsi = rsi[abs_idx]

        if prev_rsi >= 30:
            continue

        price_pct = ((cur_price - prev_price) / prev_price) * 100
        if price_pct > 0.2:
            continue

        rsi_diff = cur_rsi - prev_rsi
        if rsi_diff < 5.0:
            continue

        if rsi_diff > best_div:
            best_div = rsi_diff
            best_reason = (
                f"Bull div: price {price_pct:+.2f}% vs {bars_ago}b, "
                f"RSI {prev_rsi:.1f}->{cur_rsi:.1f} (+{rsi_diff:.1f})"
            )

    if best_div > 0:
        return True, best_reason, best_div
    return False, "", 0.0


def detect_bearish_divergence(
    close: np.ndarray,
    rsi: np.ndarray,
) -> tuple[bool, str, float]:
    """Detect bearish RSI divergence (price higher high, RSI lower high).

    Requirements:
      - Previous swing high had RSI > 70
      - Current price at/above that level (within 0.2%)
      - Current RSI at least 5 points lower
      - Current RSI > 55
      - Lookback range: 10-40 bars

    Returns (detected, reason, rsi_divergence_magnitude).
    """
    n = len(close)
    if n < 55:
        return False, "", 0.0

    cur_price = close[-1]
    cur_rsi = rsi[-1]

    if cur_rsi < 55:
        return False, "", 0.0

    start = max(0, n - 45)
    end = n - 3

    swing_indices = _find_swing_highs(close[start:end])
    if not swing_indices:
        return False, "", 0.0

    best_div = 0.0
    best_reason = ""

    for local_idx in swing_indices:
        abs_idx = start + local_idx
        bars_ago = n - 1 - abs_idx
        if bars_ago < 10 or bars_ago > 40:
            continue

        prev_price = close[abs_idx]
        prev_rsi = rsi[abs_idx]

        if prev_rsi <= 70:
            continue

        price_pct = ((cur_price - prev_price) / prev_price) * 100
        if price_pct < -0.2:
            continue

        rsi_diff = prev_rsi - cur_rsi
        if rsi_diff < 5.0:
            continue

        if rsi_diff > best_div:
            best_div = rsi_diff
            best_reason = (
                f"Bear div: price {price_pct:+.2f}% vs {bars_ago}b, "
                f"RSI {prev_rsi:.1f}->{cur_rsi:.1f} (-{rsi_diff:.1f})"
            )

    if best_div > 0:
        return True, best_reason, best_div
    return False, "", 0.0


# ─── Main strategy function ──────────────────────────────


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row) -> dict:
    """RSI Divergence Scalp strategy.

    Requires divergence + candle + 15m trend + at least one momentum confirmation.
    """
    no_signal = {"signal": 0, "reason": "no signal", "confidence": 0, "trigger": ""}

    n = len(df_5m)
    if n < 60:
        return no_signal

    close = df_5m["close"].values
    rsi = df_5m["rsi"].values
    macd_hist = df_5m["macd_hist"].values

    cur_close = row["close"]
    cur_volume_ratio = row["volume_ratio"]
    is_bullish = row["is_bullish"]
    is_bearish = row["is_bearish"]
    stoch_k = row.get("stoch_rsi_k", 0.5)
    stoch_d = row.get("stoch_rsi_d", 0.5)
    cur_bb_pct = row.get("bb_pct", 0.5)

    # ── Volume filter ─────────────────────────────────
    if cur_volume_ratio < 0.8:
        return no_signal

    # ── 15m trend ─────────────────────────────────────
    trend_bias = 0
    if len(df_15m) >= 200:
        ema50_15m = df_15m["ema50"].iloc[-1]
        ema200_15m = df_15m["ema200"].iloc[-1]
        if pd.notna(ema50_15m) and pd.notna(ema200_15m):
            if ema50_15m > ema200_15m:
                trend_bias = 1
            elif ema50_15m < ema200_15m:
                trend_bias = -1

    # ── Momentum confirmations ────────────────────────
    h = macd_hist
    macd_bull = len(h) >= 3 and h[-1] > h[-2]
    macd_bear = len(h) >= 3 and h[-1] < h[-2]

    stoch_bull = stoch_k < 0.4 and stoch_k > stoch_d
    stoch_bear = stoch_k > 0.6 and stoch_k < stoch_d

    # ── Divergences ───────────────────────────────────
    bull_div, bull_reason, bull_mag = detect_bullish_divergence(close, rsi)
    bear_div, bear_reason, bear_mag = detect_bearish_divergence(close, rsi)

    # ── BULLISH ENTRY ─────────────────────────────────
    if bull_div and is_bullish and trend_bias >= 0:
        confirmations = []
        if macd_bull:
            confirmations.append("MACD+")
        if stoch_bull:
            confirmations.append("StochRSI+")

        if not confirmations:
            return no_signal

        confidence = 1
        reason = bull_reason

        # Confidence = 2 if both momentum signals confirm, or trend + one momentum
        if len(confirmations) == 2:
            confidence = 2
        elif trend_bias == 1 and len(confirmations) >= 1:
            confidence = 2

        if trend_bias == 1:
            reason += " | 15m uptrend"
        if cur_bb_pct < 0.25:
            reason += " | BB low"

        reason += " | " + " ".join(confirmations)

        return {
            "signal": 1,
            "reason": reason,
            "confidence": confidence,
            "trigger": "rsi_bull_div",
        }

    # ── BEARISH ENTRY ─────────────────────────────────
    if bear_div and is_bearish and trend_bias <= 0:
        confirmations = []
        if macd_bear:
            confirmations.append("MACD-")
        if stoch_bear:
            confirmations.append("StochRSI-")

        if not confirmations:
            return no_signal

        confidence = 1
        reason = bear_reason

        if len(confirmations) == 2:
            confidence = 2
        elif trend_bias == -1 and len(confirmations) >= 1:
            confidence = 2

        if trend_bias == -1:
            reason += " | 15m downtrend"
        if cur_bb_pct > 0.75:
            reason += " | BB high"

        reason += " | " + " ".join(confirmations)

        return {
            "signal": -1,
            "reason": reason,
            "confidence": confidence,
            "trigger": "rsi_bear_div",
        }

    return no_signal
