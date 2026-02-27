"""Hybrid V5 - 15m Entry (Higher Timeframe Scalp)

All 5m strategies showed avg MFE of only 0.3-0.6%.
Maybe the issue is 5m noise. Try using 15m as BOTH
filter and entry timeframe (but still reading from 5m data
by using every 3rd bar to simulate 15m entries).

Logic:
- Use 15m EMA crossover for entry (every ~3 5m bars)
- 15m trend: EMA20 > EMA50 = uptrend
- Entry: EMA8 crosses EMA20 on 15m + MACD confirms + ADX > 20
- Use 5m for SL/TP exit monitoring (tighter management)
- Larger TP since 15m moves are bigger: SL=0.8%, TP=1.6%
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid V5 - 15m Entry"

BT_CONFIG = ScalpBacktestConfig(
    initial_capital=1000.0,
    leverage=3,
    position_size_pct=5.0,
    stop_loss_pct=0.8,
    take_profit_pct=1.6,
    trailing_activate_pct=0.9,
    trailing_callback_pct=0.4,
    taker_fee_pct=0.055,
    fee_buffer_pct=0.15,
    time_exit_bars=24,  # 2 hours in 5m bars
    max_daily_trades=8,
    cooldown_bars=6,
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row: pd.Series) -> dict:
    result = {"signal": 0, "reason": "No signal", "confidence": 0, "trigger": "none"}

    if df_15m.empty or len(df_15m) < 210:
        return result

    # ── Only evaluate on 15m boundaries (every 3 5m bars) ──
    # Check if current 5m timestamp aligns with 15m close
    ts = row.get("timestamp", None)
    if ts is not None and hasattr(ts, "minute"):
        # 15m boundaries: 0, 15, 30, 45
        if ts.minute % 15 != 0:
            return result

    # ── 15m Trend: EMA20 > EMA50 ──
    last_15m = df_15m.iloc[-1]
    prev_15m = df_15m.iloc[-2] if len(df_15m) > 1 else last_15m

    ema20_15m = last_15m.get("ema20", 0)
    ema50_15m = last_15m.get("ema50", 0)
    if ema20_15m == 0 or ema50_15m == 0:
        return result

    trend = 1 if ema20_15m > ema50_15m else (-1 if ema20_15m < ema50_15m else 0)
    if trend == 0:
        return result

    # ── 15m ADX > 20 ──
    adx_15m = last_15m.get("adx", 0)
    if adx_15m < 20:
        return result

    # ── 15m EMA8/EMA20 Crossover ──
    ema8_now = last_15m.get("ema8", 0)
    ema8_prev = prev_15m.get("ema8", 0)
    ema20_prev = prev_15m.get("ema20", 0)
    ema20_now = last_15m.get("ema20", 0)

    cross_long = ema8_prev <= ema20_prev and ema8_now > ema20_now
    cross_short = ema8_prev >= ema20_prev and ema8_now < ema20_now

    # Also accept established alignment (EMA8 > EMA20 > EMA50 for long)
    ema50_now = last_15m.get("ema50", 0)
    aligned_long = ema8_now > ema20_now > ema50_now
    aligned_short = ema8_now < ema20_now < ema50_now

    has_signal = False
    trigger_type = ""

    if trend == 1 and (cross_long or aligned_long):
        has_signal = True
        trigger_type = "15m_cross" if cross_long else "15m_aligned"
    elif trend == -1 and (cross_short or aligned_short):
        has_signal = True
        trigger_type = "15m_cross" if cross_short else "15m_aligned"

    if not has_signal:
        return result

    # ── 15m MACD confirmation ──
    macd_hist_15m = last_15m.get("macd_hist", 0)
    if trend == 1 and macd_hist_15m <= 0:
        # Allow if turning positive
        prev_macd = prev_15m.get("macd_hist", 0)
        if macd_hist_15m <= prev_macd:
            return result
    if trend == -1 and macd_hist_15m >= 0:
        prev_macd = prev_15m.get("macd_hist", 0)
        if macd_hist_15m >= prev_macd:
            return result

    # ── 15m RSI filter ──
    rsi_15m = last_15m.get("rsi", 50)
    if trend == 1 and rsi_15m > 70:
        return result
    if trend == -1 and rsi_15m < 30:
        return result

    # ── 5m candle confirmation ──
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    if trend == 1 and not is_bullish:
        return result
    if trend == -1 and not is_bearish:
        return result

    # ── Volume ──
    volume_ratio = row.get("volume_ratio", 1.0)
    if volume_ratio < 0.8:
        return result

    confidence = 2 if cross_long or cross_short else 1
    trigger = f"{trigger_type}_{'long' if trend == 1 else 'short'}"
    reason = (
        f"{'LONG' if trend==1 else 'SHORT'} {trigger_type} | "
        f"ADX15m={adx_15m:.0f} RSI15m={rsi_15m:.0f} Vol={volume_ratio:.1f}x"
    )

    return {"signal": trend, "reason": reason, "confidence": confidence, "trigger": trigger}
