"""Hybrid Final - Best of All Strategies Combined

Tournament results (26,000 bars, 90 days):
  Strategy 1 (EMA Momentum):   PF=1.08, +0.32%, 120 trades ← winner
  Strategy 4 (RSI Divergence):  PF=0.92, -0.34%, 79 trades ← best R:R
  Hybrid V4 (Enhanced EMA):     PF=1.07, +0.16%, 58 trades
  Hybrid V5 (15m Entry):        PF=1.01, +0.16%, 314 trades ← most volume

Key insights:
1. EMA crossover with trend filter = only consistently profitable logic
2. 15m entry produces higher MFE (0.66% vs 0.46%)
3. Aligned signals work IF properly filtered
4. TIME_EXIT is the main enemy → need trades that move quickly

Final strategy: 15m-aligned entry with 5m momentum confirmation
- Use 15m EMA alignment (not just crossover) for direction
- Require 5m EMA5 > EMA13 (momentum confirmation)
- Require 5m RSI momentum (rising for long, falling for short)
- 15m ADX > 22 for trend strength
- SL/TP calibrated to V5's MFE distribution
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid Final"

BT_CONFIG = ScalpBacktestConfig(
    initial_capital=1000.0,
    leverage=3,
    position_size_pct=5.0,
    stop_loss_pct=0.7,
    take_profit_pct=1.4,
    trailing_activate_pct=0.8,
    trailing_callback_pct=0.35,
    taker_fee_pct=0.055,
    fee_buffer_pct=0.15,
    time_exit_bars=18,  # 90 min
    max_daily_trades=10,
    cooldown_bars=6,  # 30 min
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row: pd.Series) -> dict:
    result = {"signal": 0, "reason": "No signal", "confidence": 0, "trigger": "none"}

    if len(df_5m) < 30 or df_15m.empty or len(df_15m) < 210:
        return result

    # ── 15m Trend: EMA20 > EMA50, with gap check ──
    last_15m = df_15m.iloc[-1]
    ema20_15m = last_15m.get("ema20", 0)
    ema50_15m = last_15m.get("ema50", 0)
    if ema20_15m == 0 or ema50_15m == 0:
        return result

    trend = 1 if ema20_15m > ema50_15m else (-1 if ema20_15m < ema50_15m else 0)
    if trend == 0:
        return result

    # Skip very weak trends
    trend_gap = abs(ema20_15m - ema50_15m) / ema50_15m * 100
    if trend_gap < 0.2:
        return result

    # ── 15m ADX > 22 ──
    adx_15m = last_15m.get("adx", 0)
    if adx_15m < 22:
        return result

    # ── 15m RSI sanity (not at extremes) ──
    rsi_15m = last_15m.get("rsi", 50)
    if trend == 1 and rsi_15m > 72:
        return result
    if trend == -1 and rsi_15m < 28:
        return result

    # ── 5m EMA5/EMA13 alignment (momentum confirmation) ──
    ema5 = row.get("ema5", 0)
    ema13 = row.get("ema13", 0)
    if trend == 1 and ema5 <= ema13:
        return result
    if trend == -1 and ema5 >= ema13:
        return result

    # ── 5m MACD histogram must agree ──
    macd_hist = row.get("macd_hist", 0)
    if trend == 1 and macd_hist <= 0:
        return result
    if trend == -1 and macd_hist >= 0:
        return result

    # ── 5m RSI momentum (trending, not exhausted) ──
    rsi_5m = row.get("rsi", 50)
    prev_rsi = df_5m.iloc[-2].get("rsi", 50)

    if trend == 1:
        if rsi_5m > 68:  # overbought
            return result
        if rsi_5m < prev_rsi - 3:  # losing momentum
            return result
    else:
        if rsi_5m < 32:  # oversold
            return result
        if rsi_5m > prev_rsi + 3:  # losing momentum
            return result

    # ── 5m Candle direction ──
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    if trend == 1 and not is_bullish:
        return result
    if trend == -1 and not is_bearish:
        return result

    # ── Volume not dead ──
    volume_ratio = row.get("volume_ratio", 1.0)
    if volume_ratio < 0.7:
        return result

    # ── 5m ADX also shows trend ──
    adx_5m = row.get("adx", 0)

    # ── Confidence scoring ──
    confidence = 1
    bonus = 0
    if adx_5m > 25:
        bonus += 1
    if volume_ratio > 1.3:
        bonus += 1
    if adx_15m > 30:
        bonus += 1
    if bonus >= 2:
        confidence = 2

    trigger = f"momentum_{'long' if trend == 1 else 'short'}"
    reason = (
        f"{'LONG' if trend==1 else 'SHORT'} 15m trend + 5m momentum | "
        f"ADX15m={adx_15m:.0f} ADX5m={adx_5m:.0f} RSI5m={rsi_5m:.0f} "
        f"Vol={volume_ratio:.1f}x gap={trend_gap:.2f}%"
    )

    return {"signal": trend, "reason": reason, "confidence": confidence, "trigger": trigger}
