"""Hybrid V3 - Trend Following with Pullback Entry

A different approach: instead of catching crossovers (which may be late),
enter on PULLBACKS in an established trend.

Logic:
1. 15m: EMA50 > EMA200 = uptrend
2. 5m: price pulls back to EMA20 zone (within 0.15%)
3. Bounce confirmation: bullish candle + RSI not oversold
4. Volume: above average

This is closer to the original Plan B strategy but with tighter parameters
informed by the backtest tournament results.
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid V3 - Trend Pullback"

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
    time_exit_bars=12,
    max_daily_trades=10,
    cooldown_bars=4,
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

    # ── 15m ADX (trend must be active) ──
    adx_15m = last_15m.get("adx", 0)
    if adx_15m < 20:
        return result

    # ── 5m: Check for pullback to EMA20 ──
    close = row["close"]
    ema20 = row.get("ema20", 0)
    if ema20 == 0:
        return result

    dist_pct = abs(close - ema20) / ema20 * 100

    # Pullback: price near EMA20 (within 0.15%)
    if dist_pct > 0.15:
        return result

    # ── EMA20 slope must be in trend direction ──
    ema20_prev = df_5m.iloc[-3].get("ema20", 0)
    if ema20_prev == 0:
        return result
    ema20_slope = (ema20 - ema20_prev) / ema20_prev * 100

    if trend == 1 and ema20_slope < 0:
        result["reason"] = "EMA20 declining in uptrend"
        return result
    if trend == -1 and ema20_slope > 0:
        result["reason"] = "EMA20 rising in downtrend"
        return result

    # ── Bounce confirmation ──
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    rsi = row.get("rsi", 50)
    adx_5m = row.get("adx", 0)

    if trend == 1:
        if not is_bullish:
            return result
        if rsi > 65 or rsi < 25:
            return result
        # Price should be above EMA20 (bounce, not break)
        if close < ema20:
            return result
    else:
        if not is_bearish:
            return result
        if rsi < 35 or rsi > 75:
            return result
        if close > ema20:
            return result

    # ── Volume ──
    volume_ratio = row.get("volume_ratio", 1.0)
    if volume_ratio < 0.8:
        return result

    # ── MACD direction should match ──
    macd_hist = row.get("macd_hist", 0)
    if trend == 1 and macd_hist < 0:
        # Allow if it's turning positive
        prev_macd = df_5m.iloc[-2].get("macd_hist", 0)
        if macd_hist <= prev_macd:
            return result

    if trend == -1 and macd_hist > 0:
        prev_macd = df_5m.iloc[-2].get("macd_hist", 0)
        if macd_hist >= prev_macd:
            return result

    # ── Signal ──
    confidence = 1
    if adx_5m > 25 and volume_ratio > 1.2:
        confidence = 2

    trigger = f"pullback_{'long' if trend == 1 else 'short'}"
    reason = (
        f"{'LONG' if trend==1 else 'SHORT'} pullback to EMA20 "
        f"(dist={dist_pct:.3f}%) | ADX15m={adx_15m:.0f} RSI={rsi:.0f} "
        f"Vol={volume_ratio:.1f}x"
    )

    return {"signal": trend, "reason": reason, "confidence": confidence, "trigger": trigger}
