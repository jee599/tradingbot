"""Multi-Confirm Scalp Strategy

Core idea: Require multiple confirmations from different indicator types
before entering. This reduces trade frequency but increases win rate.
Combine: EMA alignment, RSI zone, BB position, volume surge, and MACD
direction.

Architecture:
  Mandatory gates (ALL must pass):
    1. 15m Trend: EMA50 > EMA200 (or reverse)
    2. ADX >= 20 (sufficient trend)
    3. 5m EMA stack: EMA5 > EMA13 > EMA20 aligned with trend
    4. Confirming candle direction

  Scoring checks (need 3+ of 5):
    1. RSI momentum: consecutively rising/falling toward trend
    2. StochRSI: K > D for long (or K < D for short)
    3. BB position: < 0.35 long, > 0.65 short
    4. Volume: ratio > 1.2
    5. MACD: histogram increasing (long) or decreasing (short)

  Confidence: 3/5 = 1, 4+/5 = 2, ADX>25 bumps

Config: SL=0.7%, TP=1.4%, trailing=0.8%/0.35%,
        time_exit=10 bars, cooldown=4
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Multi-Confirm Scalp"

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
    time_exit_bars=10,        # 10 * 5min = 50min
    max_daily_trades=20,
    cooldown_bars=4,
    use_atr_stops=False,
)


def _safe_val(series_or_val, default=None):
    """Safely extract a numeric value, returning default if NaN or None."""
    if series_or_val is None:
        return default
    if isinstance(series_or_val, float) and (pd.isna(series_or_val) or np.isnan(series_or_val)):
        return default
    try:
        v = float(series_or_val)
        if pd.isna(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row) -> dict:
    """Multi-Confirm Scalp signal generator.

    Uses mandatory gates + scoring checks to find high-probability entries.

    Args:
        df_5m: Full 5m DataFrame with indicators (up to current bar).
        df_15m: 15m DataFrame with indicators (up to current 5m timestamp).
        row: Current 5m bar (last row of df_5m).

    Returns:
        dict with signal (+1/0/-1), reason, confidence (0-2), trigger.
    """
    result = {"signal": 0, "reason": "no signal", "confidence": 0, "trigger": ""}

    if len(df_5m) < 4:
        result["reason"] = "insufficient 5m data"
        return result
    if len(df_15m) < 2:
        result["reason"] = "insufficient 15m data"
        return result

    curr = df_5m.iloc[-1]
    prev = df_5m.iloc[-2]
    prev2 = df_5m.iloc[-3]
    row_15m = df_15m.iloc[-1]

    # ── Extract all needed values ──
    close = _safe_val(curr.get("close", None))
    open_price = _safe_val(curr.get("open", None))
    adx = _safe_val(curr.get("adx", None))
    rsi_curr = _safe_val(curr.get("rsi", None))
    rsi_prev = _safe_val(prev.get("rsi", None))
    rsi_prev2 = _safe_val(prev2.get("rsi", None))
    bb_pct = _safe_val(curr.get("bb_pct", None))
    vol_ratio = _safe_val(curr.get("volume_ratio", None), default=1.0)
    ema5 = _safe_val(curr.get("ema5", None))
    ema13 = _safe_val(curr.get("ema13", None))
    ema20 = _safe_val(curr.get("ema20", None))
    macd_hist = _safe_val(curr.get("macd_hist", None))
    macd_hist_prev = _safe_val(prev.get("macd_hist", None))
    stoch_k = _safe_val(curr.get("stoch_rsi_k", None))
    stoch_d = _safe_val(curr.get("stoch_rsi_d", None))

    ema50_15m = _safe_val(row_15m.get("ema50", None))
    ema200_15m = _safe_val(row_15m.get("ema200", None))

    # ══════════════════════════════════════════════
    #  MANDATORY GATES
    # ══════════════════════════════════════════════

    # Gate 1: ADX minimum trend
    if adx is None or adx < 20:
        result["reason"] = f"ADX low ({adx})"
        return result

    # Gate 2: 15m trend
    if ema50_15m is None or ema200_15m is None:
        result["reason"] = "15m EMAs n/a"
        return result
    trend_long = ema50_15m > ema200_15m
    trend_short = ema50_15m < ema200_15m
    if not trend_long and not trend_short:
        result["reason"] = "15m neutral"
        return result

    # Gate 3: 5m EMA stack
    if ema5 is None or ema13 is None or ema20 is None:
        result["reason"] = "5m EMAs n/a"
        return result
    if trend_long and not (ema5 > ema13 > ema20):
        result["reason"] = "5m stack not bullish"
        return result
    if trend_short and not (ema5 < ema13 < ema20):
        result["reason"] = "5m stack not bearish"
        return result

    is_long = trend_long

    # Gate 4: Candle direction
    if close is None or open_price is None:
        result["reason"] = "price n/a"
        return result
    if is_long and close <= open_price:
        result["reason"] = "bearish candle"
        return result
    if not is_long and close >= open_price:
        result["reason"] = "bullish candle"
        return result

    # ══════════════════════════════════════════════
    #  SCORING CHECKS (need 3 of 5)
    # ══════════════════════════════════════════════
    score = 0
    reasons = []

    # 1. RSI momentum
    if rsi_curr is not None and rsi_prev is not None and rsi_prev2 is not None:
        if is_long and rsi_curr > rsi_prev and rsi_prev > rsi_prev2 and rsi_curr < 65:
            score += 1
            reasons.append(f"RSI rising ({rsi_curr:.1f})")
        elif not is_long and rsi_curr < rsi_prev and rsi_prev < rsi_prev2 and rsi_curr > 35:
            score += 1
            reasons.append(f"RSI falling ({rsi_curr:.1f})")

    # 2. StochRSI direction
    if stoch_k is not None and stoch_d is not None:
        if is_long and stoch_k > stoch_d:
            score += 1
            reasons.append(f"StochK>D ({stoch_k:.2f})")
        elif not is_long and stoch_k < stoch_d:
            score += 1
            reasons.append(f"StochK<D ({stoch_k:.2f})")

    # 3. BB position
    if bb_pct is not None:
        if is_long and bb_pct < 0.35:
            score += 1
            reasons.append(f"BB low ({bb_pct:.3f})")
        elif not is_long and bb_pct > 0.65:
            score += 1
            reasons.append(f"BB high ({bb_pct:.3f})")

    # 4. Volume
    if vol_ratio > 1.2:
        score += 1
        reasons.append(f"Vol ({vol_ratio:.2f})")

    # 5. MACD momentum
    if macd_hist is not None and macd_hist_prev is not None:
        if is_long and macd_hist > macd_hist_prev:
            score += 1
            reasons.append(f"MACD up ({macd_hist:.6f})")
        elif not is_long and macd_hist < macd_hist_prev:
            score += 1
            reasons.append(f"MACD dn ({macd_hist:.6f})")

    # ══════════════════════════════════════════════
    #  DECISION
    # ══════════════════════════════════════════════
    if score < 3:
        result["reason"] = f"score {score}/5 < 3"
        return result

    direction = "LONG" if is_long else "SHORT"
    confidence = 1 if score == 3 else 2
    if adx > 25:
        confidence = min(confidence + 1, 2)

    trigger = f"multi_confirm_{direction.lower()}_{score}of5"

    return {
        "signal": 1 if is_long else -1,
        "reason": f"{direction} gates+{score}/5: {'; '.join(reasons)}",
        "confidence": confidence,
        "trigger": trigger,
    }
