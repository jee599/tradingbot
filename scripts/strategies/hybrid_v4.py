"""Hybrid V4 - Enhanced EMA Momentum

The original EMA Momentum was the ONLY profitable strategy (PF=1.08, +0.32%).
V2/V3 failed because they changed the SL/TP parameters.

V4 keeps the EXACT winning parameters from EMA Momentum:
  SL=0.6%, TP=1.2%, trailing=0.7%/0.3%, time_exit=12 bars

Then adds MINIMAL quality filters:
1. Bullish/bearish candle must match signal direction
2. ATR filter: only trade when volatility is above average (bigger moves)
3. 15m trend gap: avoid weak/ambiguous trends

These filters should remove some losing trades without
killing winning ones, pushing PF above 1.08.
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid V4 - Enhanced EMA Momentum"

# EXACTLY the same config as the winning EMA Momentum strategy
BT_CONFIG = ScalpBacktestConfig(
    initial_capital=1000.0,
    leverage=3,
    position_size_pct=5.0,
    stop_loss_pct=0.6,
    take_profit_pct=1.2,
    trailing_activate_pct=0.7,
    trailing_callback_pct=0.3,
    taker_fee_pct=0.055,
    fee_buffer_pct=0.15,
    time_exit_bars=12,  # 60 min
    max_daily_trades=10,
    cooldown_bars=3,
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

    # ── [NEW] 15m trend gap: skip weak trends ──
    trend_gap_pct = abs(ema50_15m - ema200_15m) / ema200_15m * 100
    if trend_gap_pct < 0.5:
        return result

    # ── ADX > 20 (same as original) ──
    adx = row.get("adx", 0)
    if adx < 20:
        return result

    # ── [NEW] ATR filter: only trade when volatility above average ──
    atr = row.get("atr", 0)
    close = row["close"]
    if close > 0 and atr > 0:
        atr_pct = atr / close * 100
        # Skip when ATR% is below 0.15% (too calm for 5m scalp)
        if atr_pct < 0.15:
            return result

    # ── EMA5/EMA13 Crossover (exact same as EMA Momentum) ──
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

    # ── MACD confirmation (same as original) ──
    macd_hist = row.get("macd_hist", 0)
    if trend == 1 and macd_hist <= 0:
        return result
    if trend == -1 and macd_hist >= 0:
        return result

    # ── RSI filter (same as original) ──
    rsi = row.get("rsi", 50)
    if trend == 1 and rsi > 70:
        return result
    if trend == -1 and rsi < 30:
        return result

    # ── Volume (same as original) ──
    volume_ratio = row.get("volume_ratio", 1.0)
    if volume_ratio < 1.0:
        return result

    # ── [NEW] Candle direction must match ──
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    if trend == 1 and not is_bullish:
        return result
    if trend == -1 and not is_bearish:
        return result

    # ── Signal ──
    confidence = 1
    if adx > 30 and volume_ratio > 1.5:
        confidence = 2

    trigger = f"ema_cross_{'long' if trend == 1 else 'short'}"
    reason = (
        f"{'LONG' if trend==1 else 'SHORT'} EMA cross | ADX={adx:.0f} RSI={rsi:.0f} "
        f"Vol={volume_ratio:.1f}x TrendGap={trend_gap_pct:.2f}%"
    )

    return {"signal": trend, "reason": reason, "confidence": confidence, "trigger": trigger}
