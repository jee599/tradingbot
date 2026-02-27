"""VWAP Bounce Scalp Strategy.

Core idea: Trade bounces off VWAP on 5m timeframe.
- Price pulling back to VWAP in an uptrend -> long
- Price pulling back to VWAP in a downtrend -> short
- Uses EMA20 as dynamic support/resistance confirmation

15m trend filter: EMA50 > EMA200 = uptrend (long bias), opposite = downtrend
5m VWAP bounce long: price near VWAP -> bounces above + close > vwap + close > ema20
                     + bullish candle + RSI between 40-60
5m VWAP bounce short: reverse conditions
Volume filter: volume_ratio > 0.8
MACD histogram confirms direction

Config: SL=0.7%, TP=1.2%, trailing activate=0.8%, callback=0.4%, time_exit=10 bars
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "VWAP Bounce Scalp"

BT_CONFIG = ScalpBacktestConfig(
    stop_loss_pct=0.7,
    take_profit_pct=1.2,
    trailing_activate_pct=0.8,
    trailing_callback_pct=0.4,
    time_exit_bars=10,  # 50 min
    fee_buffer_pct=0.15,
    cooldown_bars=3,
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row) -> dict:
    """VWAP Bounce scalp strategy.

    Args:
        df_5m: Full 5m DataFrame with indicators (up to current bar).
        df_15m: 15m DataFrame with indicators (up to current bar).
        row: Current 5m bar (last row of df_5m).

    Returns:
        {"signal": +1/0/-1, "reason": str, "confidence": int, "trigger": str}
    """
    result = {"signal": 0, "reason": "no signal", "confidence": 0, "trigger": ""}

    if len(df_5m) < 10 or len(df_15m) < 5:
        result["reason"] = "insufficient data"
        return result

    # ── 15m Trend Filter ──────────────────────────────
    row_15m = df_15m.iloc[-1]
    ema50_15m = row_15m.get("ema50", np.nan)
    ema200_15m = row_15m.get("ema200", np.nan)

    if pd.isna(ema50_15m) or pd.isna(ema200_15m):
        result["reason"] = "15m EMAs not available"
        return result

    uptrend_15m = ema50_15m > ema200_15m
    downtrend_15m = ema50_15m < ema200_15m

    if not uptrend_15m and not downtrend_15m:
        result["reason"] = "15m trend neutral (EMA50 == EMA200)"
        return result

    # ── 5m indicator values ───────────────────────────
    close = row["close"]
    vwap = row.get("vwap", np.nan)
    ema20 = row.get("ema20", np.nan)
    rsi = row.get("rsi", np.nan)
    volume_ratio = row.get("volume_ratio", np.nan)
    is_bullish = bool(row.get("is_bullish", False))
    is_bearish = bool(row.get("is_bearish", False))
    macd_hist = row.get("macd_hist", np.nan)

    if pd.isna(vwap) or pd.isna(ema20) or pd.isna(rsi) or pd.isna(volume_ratio):
        result["reason"] = "5m indicators not available"
        return result

    if vwap <= 0:
        result["reason"] = "VWAP invalid"
        return result

    # ── VWAP proximity check (last 3 bars) ────────────
    # Price was within 0.2% of VWAP in last 3 bars (pullback to VWAP confirmation)
    vwap_proximity = False
    lookback = min(3, len(df_5m) - 1)
    for j in range(len(df_5m) - lookback - 1, len(df_5m) - 1):
        bar = df_5m.iloc[j]
        bv = bar.get("vwap", np.nan)
        if pd.notna(bv) and bv > 0:
            if bar["low"] <= bv <= bar["high"]:
                vwap_proximity = True
                break
            if abs(bar["close"] - bv) / bv * 100 <= 0.2:
                vwap_proximity = True
                break

    if not vwap_proximity:
        result["reason"] = "no VWAP proximity in last 3 bars"
        return result

    # ── Volume filter ─────────────────────────────────
    if volume_ratio < 0.8:
        result["reason"] = f"low volume (ratio={volume_ratio:.2f} < 0.8)"
        return result

    # ── RSI neutral zone (40-60) ──────────────────────
    if rsi < 40 or rsi > 60:
        result["reason"] = f"RSI out of neutral zone ({rsi:.1f})"
        return result

    # ── Current VWAP relationship ─────────────────────
    above_vwap = close > vwap
    below_vwap = close < vwap

    # ── Long signal ───────────────────────────────────
    # 5m VWAP bounce long: close > vwap + close > ema20 + bullish + RSI 40-60
    if uptrend_15m and above_vwap and close > ema20 and is_bullish:
        confidence = 1
        reasons = []
        reasons.append(f"VWAP bounce up (close={close:.4f} > vwap={vwap:.4f})")
        reasons.append(f"15m uptrend (EMA50={ema50_15m:.4f} > EMA200={ema200_15m:.4f})")
        reasons.append(f"above EMA20={ema20:.4f}")
        reasons.append(f"RSI={rsi:.1f}")
        reasons.append(f"vol_ratio={volume_ratio:.2f}")

        # MACD histogram confirms direction
        if pd.notna(macd_hist) and macd_hist > 0:
            confidence = 2
            reasons.append(f"MACD hist positive")

        return {
            "signal": 1,
            "reason": "; ".join(reasons),
            "confidence": min(confidence, 2),
            "trigger": "vwap_bounce_long",
        }

    # ── Short signal ──────────────────────────────────
    # 5m VWAP bounce short: close < vwap + close < ema20 + bearish + RSI 40-60
    if downtrend_15m and below_vwap and close < ema20 and is_bearish:
        confidence = 1
        reasons = []
        reasons.append(f"VWAP bounce down (close={close:.4f} < vwap={vwap:.4f})")
        reasons.append(f"15m downtrend (EMA50={ema50_15m:.4f} < EMA200={ema200_15m:.4f})")
        reasons.append(f"below EMA20={ema20:.4f}")
        reasons.append(f"RSI={rsi:.1f}")
        reasons.append(f"vol_ratio={volume_ratio:.2f}")

        # MACD histogram confirms direction
        if pd.notna(macd_hist) and macd_hist < 0:
            confidence = 2
            reasons.append(f"MACD hist negative")

        return {
            "signal": -1,
            "reason": "; ".join(reasons),
            "confidence": min(confidence, 2),
            "trigger": "vwap_bounce_short",
        }

    result["reason"] = f"conditions not fully met"
    return result
