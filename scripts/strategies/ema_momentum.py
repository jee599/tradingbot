"""EMA Momentum Scalp Strategy

Core idea: Use fast EMA crossovers (EMA5/EMA13) on 5m for entry,
with 15m EMA50/EMA200 as trend filter. Require MACD histogram
confirmation and ADX > 20.

Rules:
- 15m trend filter: EMA50 > EMA200 => long only, EMA50 < EMA200 => short only
- 5m entry: EMA5 crosses above EMA13 (long) or below (short)
- Confirmation: MACD histogram same direction, ADX > 20
- RSI filter: RSI < 70 for long, RSI > 30 for short
- Volume: volume_ratio > 1.0
"""

from __future__ import annotations

import pandas as pd

from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "EMA Momentum Scalp"

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
    time_exit_bars=12,       # 12 * 5min = 60min
    max_daily_trades=20,
    cooldown_bars=3,
    use_atr_stops=False,
)


def strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, row) -> dict:
    """EMA Momentum Scalp signal generator.

    Args:
        df_5m: Full 5m DataFrame with indicators (up to current bar).
        df_15m: 15m DataFrame with indicators (up to current 5m timestamp).
        row: Current 5m bar (last row of df_5m).

    Returns:
        dict with signal (+1/0/-1), reason, confidence (0-2), trigger.
    """
    result = {"signal": 0, "reason": "no signal", "confidence": 0, "trigger": ""}

    # Need at least 3 bars for crossover detection
    if len(df_5m) < 3:
        result["reason"] = "insufficient 5m data"
        return result

    if len(df_15m) < 2:
        result["reason"] = "insufficient 15m data"
        return result

    # ── 15m Trend Filter ──
    row_15m = df_15m.iloc[-1]
    ema50_15m = row_15m.get("ema50", None)
    ema200_15m = row_15m.get("ema200", None)

    if ema50_15m is None or ema200_15m is None:
        result["reason"] = "15m EMAs not available"
        return result

    if pd.isna(ema50_15m) or pd.isna(ema200_15m):
        result["reason"] = "15m EMAs are NaN"
        return result

    trend_long = ema50_15m > ema200_15m
    trend_short = ema50_15m < ema200_15m

    if not trend_long and not trend_short:
        result["reason"] = "15m trend neutral (EMA50 == EMA200)"
        return result

    # ── 5m EMA Crossover Detection ──
    curr = df_5m.iloc[-1]
    prev = df_5m.iloc[-2]

    ema5_curr = curr.get("ema5", None)
    ema13_curr = curr.get("ema13", None)
    ema5_prev = prev.get("ema5", None)
    ema13_prev = prev.get("ema13", None)

    if any(v is None or (isinstance(v, float) and pd.isna(v))
           for v in [ema5_curr, ema13_curr, ema5_prev, ema13_prev]):
        result["reason"] = "5m EMA5/EMA13 not available"
        return result

    cross_up = (ema5_prev <= ema13_prev) and (ema5_curr > ema13_curr)
    cross_down = (ema5_prev >= ema13_prev) and (ema5_curr < ema13_curr)

    if not cross_up and not cross_down:
        result["reason"] = "no EMA5/EMA13 crossover"
        return result

    # ── Direction Alignment with Trend ──
    if cross_up and not trend_long:
        result["reason"] = f"bullish cross but 15m trend is bearish"
        return result
    if cross_down and not trend_short:
        result["reason"] = f"bearish cross but 15m trend is bullish"
        return result

    # ── MACD Histogram Confirmation ──
    macd_hist = curr.get("macd_hist", None)
    if macd_hist is None or (isinstance(macd_hist, float) and pd.isna(macd_hist)):
        result["reason"] = "MACD histogram not available"
        return result

    if cross_up and macd_hist <= 0:
        result["reason"] = f"bullish cross but MACD hist negative ({macd_hist:.6f})"
        return result
    if cross_down and macd_hist >= 0:
        result["reason"] = f"bearish cross but MACD hist positive ({macd_hist:.6f})"
        return result

    # ── ADX Filter ──
    adx = curr.get("adx", None)
    if adx is None or (isinstance(adx, float) and pd.isna(adx)):
        result["reason"] = "ADX not available"
        return result

    if adx < 20:
        result["reason"] = f"ADX too low ({adx:.1f} < 20)"
        return result

    # ── RSI Filter ──
    rsi = curr.get("rsi", None)
    if rsi is None or (isinstance(rsi, float) and pd.isna(rsi)):
        result["reason"] = "RSI not available"
        return result

    if cross_up and rsi >= 70:
        result["reason"] = f"bullish cross but RSI overbought ({rsi:.1f})"
        return result
    if cross_down and rsi <= 30:
        result["reason"] = f"bearish cross but RSI oversold ({rsi:.1f})"
        return result

    # ── Volume Filter ──
    vol_ratio = curr.get("volume_ratio", 1.0)
    if isinstance(vol_ratio, float) and pd.isna(vol_ratio):
        vol_ratio = 1.0

    if vol_ratio < 1.0:
        result["reason"] = f"volume too low (ratio={vol_ratio:.2f} < 1.0)"
        return result

    # ── All conditions passed - generate signal ──
    confidence = 1
    reasons = []

    if cross_up:
        signal = 1
        direction = "LONG"
        reasons.append(f"EMA5 crossed above EMA13")
        reasons.append(f"15m uptrend (EMA50>{ema50_15m:.4f} > EMA200>{ema200_15m:.4f})")
    else:
        signal = -1
        direction = "SHORT"
        reasons.append(f"EMA5 crossed below EMA13")
        reasons.append(f"15m downtrend (EMA50<{ema50_15m:.4f} < EMA200<{ema200_15m:.4f})")

    reasons.append(f"MACD hist={macd_hist:.6f}")
    reasons.append(f"ADX={adx:.1f}")
    reasons.append(f"RSI={rsi:.1f}")
    reasons.append(f"Vol ratio={vol_ratio:.2f}")

    # Higher confidence if more conditions strongly align
    if adx > 30 and vol_ratio > 1.5:
        confidence = 2

    trigger = f"ema_cross_{direction.lower()}"

    return {
        "signal": signal,
        "reason": f"{direction}: {'; '.join(reasons)}",
        "confidence": confidence,
        "trigger": trigger,
    }
