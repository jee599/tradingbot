"""Hybrid V2 - Strict EMA Crossover + Quality Filters

Key lessons from V1 (739 trades, PF=0.73):
- EMA alignment alone (not crossover) generates too many low-quality entries
- Need stricter confirmation requirements
- EMA Momentum won with only 120 trades - SELECTIVITY is key

V2 changes:
- ONLY EMA crossover (not alignment) - drastically reduces signals
- Require MACD AND RSI confirmation (both, not either)
- Volume > 1.0 required
- Tighter TP at 0.8% (matching avg MFE ~0.5%)
- Aggressive trailing from 0.4% to lock profits early
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid V2 - Strict Cross"

BT_CONFIG = ScalpBacktestConfig(
    initial_capital=1000.0,
    leverage=3,
    position_size_pct=5.0,
    stop_loss_pct=0.5,
    take_profit_pct=0.8,
    trailing_activate_pct=0.4,
    trailing_callback_pct=0.2,
    taker_fee_pct=0.055,
    fee_buffer_pct=0.12,
    time_exit_bars=12,  # 60 min
    max_daily_trades=10,
    cooldown_bars=6,  # 30 min cooldown
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row: pd.Series) -> dict:
    result = {"signal": 0, "reason": "No signal", "confidence": 0, "trigger": "none"}

    if len(df_5m) < 30 or df_15m.empty or len(df_15m) < 210:
        return result

    # ── 15m Trend Filter ──
    last_15m = df_15m.iloc[-1]
    ema50_15m = last_15m.get("ema50", 0)
    ema200_15m = last_15m.get("ema200", 0)
    if ema50_15m == 0 or ema200_15m == 0:
        return result

    trend = 1 if ema50_15m > ema200_15m else (-1 if ema50_15m < ema200_15m else 0)
    if trend == 0:
        return result

    # ── 15m trend strength (EMA gap) ──
    trend_gap_pct = abs(ema50_15m - ema200_15m) / ema200_15m * 100
    if trend_gap_pct < 0.3:
        result["reason"] = f"15m trend too weak ({trend_gap_pct:.2f}%)"
        return result

    # ── ADX > 22 ──
    adx = row.get("adx", 0)
    if adx < 22:
        return result

    # ── EMA5/EMA13 CROSSOVER (strict - must be actual cross) ──
    prev = df_5m.iloc[-2]
    ema5_now = row.get("ema5", 0)
    ema13_now = row.get("ema13", 0)
    ema5_prev = prev.get("ema5", 0)
    ema13_prev = prev.get("ema13", 0)

    cross_long = ema5_prev <= ema13_prev and ema5_now > ema13_now
    cross_short = ema5_prev >= ema13_prev and ema5_now < ema13_now

    if trend == 1 and not cross_long:
        return result
    if trend == -1 and not cross_short:
        return result

    # ── MACD Confirmation (required) ──
    macd_hist = row.get("macd_hist", 0)
    if trend == 1 and macd_hist <= 0:
        result["reason"] = "MACD hist negative, skip long"
        return result
    if trend == -1 and macd_hist >= 0:
        result["reason"] = "MACD hist positive, skip short"
        return result

    # ── RSI Filter (required) ──
    rsi = row.get("rsi", 50)
    if trend == 1 and rsi > 65:
        result["reason"] = f"RSI={rsi:.0f} overbought"
        return result
    if trend == -1 and rsi < 35:
        result["reason"] = f"RSI={rsi:.0f} oversold"
        return result

    # ── Volume (required) ──
    volume_ratio = row.get("volume_ratio", 1.0)
    if volume_ratio < 1.0:
        result["reason"] = f"Low volume={volume_ratio:.1f}x"
        return result

    # ── Candle direction should match ──
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    if trend == 1 and not is_bullish:
        result["reason"] = "Bearish candle on long cross"
        return result
    if trend == -1 and not is_bearish:
        result["reason"] = "Bullish candle on short cross"
        return result

    # ── Signal ──
    confidence = 1
    if adx > 30 and volume_ratio > 1.5:
        confidence = 2

    trigger = f"strict_cross_{'long' if trend == 1 else 'short'}"
    reason = f"{'LONG' if trend==1 else 'SHORT'} cross | ADX={adx:.0f} RSI={rsi:.0f} MACD={macd_hist:.5f} Vol={volume_ratio:.1f}x"

    return {"signal": trend, "reason": reason, "confidence": confidence, "trigger": trigger}
