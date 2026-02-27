"""Hybrid Optimized Scalp Strategy

Designed from a 5-agent tournament where each agent independently developed
and backtested a scalping strategy on XRP/USDT 5m data. This hybrid combines
the winning elements:

Key insights from the tournament:
1. EMA Momentum (only profitable strategy): EMA crossover + ADX filter works
2. RSI Divergence (best R:R at 2.2:1): divergence detection adds quality
3. Mean Reversion (highest win rate 39%): BB extremes provide timing
4. Multi-Confirm showed over-filtering hurts - keep it simple
5. TIME_EXIT dominated all strategies - need realistic TP targets

Hybrid approach:
- Primary: EMA5/EMA13 crossover on 5m (from Strategy 1 - the only winner)
- Filter: 15m trend via EMA50/EMA200 (universal)
- Quality gate: ADX > 22 (from EMA Momentum success)
- Timing boost: BB extremes or RSI momentum for entry precision
- Realistic targets: TP based on ATR, tighter than 1% to match actual MFE
- Adaptive trailing: trail from 0.5% profit to capture more wins
"""

from __future__ import annotations
import pandas as pd
from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Hybrid Optimized Scalp"

BT_CONFIG = ScalpBacktestConfig(
    initial_capital=1000.0,
    leverage=3,
    position_size_pct=5.0,
    # Key insight: avg MFE is 0.4-0.6%, so TP must be reachable
    stop_loss_pct=0.5,
    take_profit_pct=0.9,
    # Early trailing to lock in small wins (reduces TIME_EXIT problem)
    trailing_activate_pct=0.45,
    trailing_callback_pct=0.25,
    taker_fee_pct=0.055,
    fee_buffer_pct=0.12,
    # Longer time window to let trades develop
    time_exit_bars=15,  # 75 min
    max_daily_trades=15,
    cooldown_bars=4,
    use_atr_stops=False,
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row: pd.Series) -> dict:
    """Hybrid scalping signal generator.

    Combines EMA crossover (momentum) + BB/RSI (timing) with strict 15m filter.
    """
    result = {"signal": 0, "reason": "No signal", "confidence": 0, "trigger": "none"}

    if len(df_5m) < 30 or df_15m.empty or len(df_15m) < 210:
        return result

    # ── 15m Trend Filter (MANDATORY) ──
    last_15m = df_15m.iloc[-1]
    ema50_15m = last_15m.get("ema50", 0)
    ema200_15m = last_15m.get("ema200", 0)

    if ema50_15m == 0 or ema200_15m == 0:
        return result

    if ema50_15m > ema200_15m:
        trend = 1
    elif ema50_15m < ema200_15m:
        trend = -1
    else:
        return result

    # ── 5m ADX Gate (from EMA Momentum success) ──
    adx = row.get("adx", 0)
    if adx < 22:
        result["reason"] = f"ADX={adx:.1f}<22, no trend"
        return result

    # ── Primary Signal: EMA5/EMA13 Crossover ──
    prev = df_5m.iloc[-2]
    ema5_now = row.get("ema5", 0)
    ema13_now = row.get("ema13", 0)
    ema5_prev = prev.get("ema5", 0)
    ema13_prev = prev.get("ema13", 0)

    ema_cross_long = ema5_prev <= ema13_prev and ema5_now > ema13_now
    ema_cross_short = ema5_prev >= ema13_prev and ema5_now < ema13_now

    # Also check EMA alignment (weaker signal but more common)
    ema20 = row.get("ema20", 0)
    ema_aligned_long = ema5_now > ema13_now > ema20 and ema5_now > ema5_prev
    ema_aligned_short = ema5_now < ema13_now < ema20 and ema5_now < ema5_prev

    has_ema_signal = False
    ema_trigger = ""

    if trend == 1 and (ema_cross_long or ema_aligned_long):
        has_ema_signal = True
        ema_trigger = "ema_cross" if ema_cross_long else "ema_aligned"
    elif trend == -1 and (ema_cross_short or ema_aligned_short):
        has_ema_signal = True
        ema_trigger = "ema_cross" if ema_cross_short else "ema_aligned"

    if not has_ema_signal:
        result["reason"] = f"No EMA signal (trend={trend})"
        return result

    # ── Timing Confirmations (need at least 1 of 3) ──
    rsi = row.get("rsi", 50)
    bb_pct = row.get("bb_pct", 0.5)
    macd_hist = row.get("macd_hist", 0)
    macd_hist_prev = prev.get("macd_hist", 0)
    volume_ratio = row.get("volume_ratio", 1.0)
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)

    confirmations = 0
    conf_reasons = []

    if trend == 1:
        # RSI: not overbought and showing momentum
        if 30 <= rsi <= 62:
            confirmations += 1
            conf_reasons.append(f"RSI={rsi:.0f}")

        # BB: in lower half (value zone)
        if bb_pct < 0.45:
            confirmations += 1
            conf_reasons.append(f"BB%={bb_pct:.2f}")

        # MACD: histogram positive or turning positive
        if macd_hist > 0 or (macd_hist > macd_hist_prev):
            confirmations += 1
            conf_reasons.append("MACD+")

        # Volume
        if volume_ratio > 1.0:
            confirmations += 1
            conf_reasons.append(f"Vol={volume_ratio:.1f}x")

        # Candle
        if is_bullish:
            confirmations += 1
            conf_reasons.append("Bull")

    elif trend == -1:
        if 38 <= rsi <= 70:
            confirmations += 1
            conf_reasons.append(f"RSI={rsi:.0f}")

        if bb_pct > 0.55:
            confirmations += 1
            conf_reasons.append(f"BB%={bb_pct:.2f}")

        if macd_hist < 0 or (macd_hist < macd_hist_prev):
            confirmations += 1
            conf_reasons.append("MACD-")

        if volume_ratio > 1.0:
            confirmations += 1
            conf_reasons.append(f"Vol={volume_ratio:.1f}x")

        if is_bearish:
            confirmations += 1
            conf_reasons.append("Bear")

    # Need at least 2 confirmations (not too strict, not too loose)
    if confirmations < 2:
        result["reason"] = f"Only {confirmations}/5 confirms ({', '.join(conf_reasons)})"
        return result

    # ── Generate Signal ──
    confidence = 1 if confirmations <= 3 else 2
    # Bonus for EMA crossover (stronger than alignment)
    if ema_trigger == "ema_cross":
        confidence = min(confidence + 1, 2)

    trigger = f"{ema_trigger}_{'long' if trend == 1 else 'short'}"
    reason = f"{'LONG' if trend==1 else 'SHORT'} {ema_trigger} | ADX={adx:.0f} | {', '.join(conf_reasons)} ({confirmations}/5)"

    result["signal"] = trend
    result["reason"] = reason
    result["confidence"] = confidence
    result["trigger"] = trigger

    return result
