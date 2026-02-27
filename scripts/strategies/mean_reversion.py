"""Mean Reversion Bollinger Band Scalp Strategy (final).

Core idea: Trade bounces from Bollinger Band extremes on 5m timeframe.
When price touches/pierces the lower BB, go long. When upper BB, go short.
Use RSI, StochRSI, MACD, volume, and 15m trend as confirmation.

Strategy characteristics:
- Uses ATR-based dynamic stops (wider SL for noise tolerance, tighter TP for realism)
- Scoring system requires 5+ confirmations for high-quality entries
- 15m trend alignment mandatory (no counter-trend trades)
- BB squeeze filtered (no trades in low-volatility compression)
- Two-bar reversal or direct band-extreme reversal candle patterns
"""

from __future__ import annotations

import numpy as np

from scripts.backtest_scalp import ScalpBacktestConfig

STRATEGY_NAME = "Mean Reversion BB Scalp"

BT_CONFIG = ScalpBacktestConfig(
    stop_loss_pct=0.5,
    take_profit_pct=1.0,
    trailing_activate_pct=0.6,
    trailing_callback_pct=0.3,
    time_exit_bars=8,         # 8 * 5min = 40min
    fee_buffer_pct=0.15,
    cooldown_bars=3,
    use_atr_stops=True,
    atr_sl_mult=1.5,          # wider SL -- survive noise
    atr_tp_mult=1.8,          # TP slightly > SL for positive expectancy
)


def strategy(df_5m, df_15m, row) -> dict:
    """Mean reversion BB scalp -- scoring system with ATR stops."""
    no_signal = {"signal": 0, "reason": "", "confidence": 0, "trigger": ""}

    if len(df_5m) < 120 or len(df_15m) < 30:
        return no_signal

    # ── Current bar ─────────────────────────────────────────────
    bb_pct = row.get("bb_pct")
    bb_width = row.get("bb_width")
    bb_lower = row.get("bb_lower")
    bb_upper = row.get("bb_upper")
    bb_mid = row.get("bb_mid")
    rsi = row.get("rsi")
    rsi_6 = row.get("rsi_6")
    stoch_k = row.get("stoch_rsi_k")
    stoch_d = row.get("stoch_rsi_d")
    adx = row.get("adx")
    macd_hist = row.get("macd_hist")
    volume_ratio = row.get("volume_ratio", 1.0)
    close = row.get("close")
    open_ = row.get("open")
    low = row.get("low")
    high = row.get("high")

    if bb_pct is None or rsi is None or close is None:
        return no_signal

    # ── Previous bar ────────────────────────────────────────────
    prev = df_5m.iloc[-2]
    prev_bb_pct = prev.get("bb_pct")
    prev_low = prev.get("low")
    prev_high = prev.get("high")
    prev_close = prev.get("close")
    prev_bb_lower = prev.get("bb_lower")
    prev_bb_upper = prev.get("bb_upper")
    prev_macd_hist = prev.get("macd_hist")
    prev_rsi = prev.get("rsi")

    if prev_bb_pct is None or np.isnan(prev_bb_pct):
        return no_signal

    # ── Filters ─────────────────────────────────────────────────

    # Squeeze filter
    if bb_width is not None:
        recent_widths = df_5m["bb_width"].iloc[-100:]
        if len(recent_widths) >= 100:
            sq = recent_widths.quantile(0.20)
            if bb_width < sq:
                return no_signal

    # Minimum volume
    if volume_ratio < 0.5:
        return no_signal

    # ── 15m Trend ───────────────────────────────────────────────
    last_15m = df_15m.iloc[-1]
    ema20_15m = last_15m.get("ema20")
    ema50_15m = last_15m.get("ema50")
    bb_pct_15m = last_15m.get("bb_pct")

    if ema20_15m is not None and ema50_15m is not None and ema50_15m > 0:
        if ema20_15m > ema50_15m:
            trend_15m = 1
        elif ema20_15m < ema50_15m:
            trend_15m = -1
        else:
            trend_15m = 0
    else:
        trend_15m = 0

    # ── Momentum helpers ────────────────────────────────────────
    macd_improving = (macd_hist is not None and prev_macd_hist is not None and
                      not np.isnan(macd_hist) and not np.isnan(prev_macd_hist) and
                      macd_hist > prev_macd_hist)
    macd_worsening = (macd_hist is not None and prev_macd_hist is not None and
                      not np.isnan(macd_hist) and not np.isnan(prev_macd_hist) and
                      macd_hist < prev_macd_hist)

    stoch_cross_up = (stoch_k is not None and stoch_d is not None and
                      stoch_k > stoch_d and stoch_k < 0.30)
    stoch_cross_down = (stoch_k is not None and stoch_d is not None and
                        stoch_k < stoch_d and stoch_k > 0.70)

    rsi_bouncing_up = (rsi is not None and prev_rsi is not None and
                       not np.isnan(prev_rsi) and rsi > prev_rsi and prev_rsi < 35)
    rsi_bouncing_down = (rsi is not None and prev_rsi is not None and
                         not np.isnan(prev_rsi) and rsi < prev_rsi and prev_rsi > 65)

    # ── Band patterns ───────────────────────────────────────────
    prev_touched_lower = (prev_bb_lower is not None and prev_low is not None and
                          prev_low <= prev_bb_lower * 1.001)
    prev_touched_upper = (prev_bb_upper is not None and prev_high is not None and
                          prev_high >= prev_bb_upper * 0.999)

    at_lower = bb_pct < 0.08
    at_upper = bb_pct > 0.92

    # Candle patterns
    body = abs(close - open_)
    candle_range = high - low if high > low else 0.0001
    wick_upper = high - max(close, open_)
    wick_lower = min(close, open_) - low

    bullish_reversal = close > open_ and body > candle_range * 0.25
    bearish_reversal = close < open_ and body > candle_range * 0.25
    hammer = close > open_ and wick_lower > body * 1.5 and wick_upper < body * 0.5
    shooting_star = close < open_ and wick_upper > body * 1.5 and wick_lower < body * 0.5

    # ── LONG Signal ─────────────────────────────────────────────
    signal = 0
    reason_parts = []
    confidence = 0
    trigger = ""

    long_setup = ((prev_touched_lower or at_lower) and
                  (bullish_reversal or hammer) and
                  rsi < 45)

    if long_setup and trend_15m >= 0:
        score = 0
        details = []

        # BB extreme (core condition -- weighted heavily)
        if bb_pct < 0.03:
            score += 3
            details.append(f"BB%={bb_pct:.3f}!!!")
        elif bb_pct < 0.08:
            score += 2
            details.append(f"BB%={bb_pct:.3f}")
        else:
            score += 1
            details.append(f"BB touch")

        # RSI extreme
        if rsi < 20:
            score += 2
            details.append(f"RSI={rsi:.0f}!!!")
        elif rsi < 30:
            score += 1
            details.append(f"RSI={rsi:.0f}")

        # Candle pattern quality
        if hammer:
            score += 1
            details.append("HAMMER")

        # RSI momentum
        if rsi_bouncing_up:
            score += 1
            details.append("RSI bounce")

        # StochRSI
        if stoch_cross_up:
            score += 1
            details.append("StRSI Xup")
        elif stoch_k is not None and stoch_k < 0.10:
            score += 1
            details.append(f"StK={stoch_k:.2f}")

        # MACD
        if macd_improving:
            score += 1
            details.append("MACD+")

        # Volume spike
        if volume_ratio > 2.0:
            score += 2
            details.append(f"V={volume_ratio:.1f}x!!!")
        elif volume_ratio > 1.3:
            score += 1
            details.append(f"V={volume_ratio:.1f}x")

        # 15m aligned
        if trend_15m == 1:
            score += 1
            details.append("15m BULL")

        # 15m BB also oversold
        if bb_pct_15m is not None and bb_pct_15m < 0.25:
            score += 1
            details.append("15m BB low")

        # VWAP
        vwap = row.get("vwap")
        if vwap is not None and close < vwap * 0.998:
            score += 1
            details.append("<VWAP")

        if score >= 5:
            signal = 1
            confidence = 2 if score >= 7 else 1
            trigger = "bb_lower_bounce"
            reason_parts = details

    # ── SHORT Signal ────────────────────────────────────────────
    if signal == 0:
        short_setup = ((prev_touched_upper or at_upper) and
                       (bearish_reversal or shooting_star) and
                       rsi > 55)

        if short_setup and trend_15m <= 0:
            score = 0
            details = []

            if bb_pct > 0.97:
                score += 3
                details.append(f"BB%={bb_pct:.3f}!!!")
            elif bb_pct > 0.92:
                score += 2
                details.append(f"BB%={bb_pct:.3f}")
            else:
                score += 1
                details.append("BB touch")

            if rsi > 80:
                score += 2
                details.append(f"RSI={rsi:.0f}!!!")
            elif rsi > 70:
                score += 1
                details.append(f"RSI={rsi:.0f}")

            if shooting_star:
                score += 1
                details.append("SHOOT_STAR")

            if rsi_bouncing_down:
                score += 1
                details.append("RSI drop")

            if stoch_cross_down:
                score += 1
                details.append("StRSI Xdn")
            elif stoch_k is not None and stoch_k > 0.90:
                score += 1
                details.append(f"StK={stoch_k:.2f}")

            if macd_worsening:
                score += 1
                details.append("MACD-")

            if volume_ratio > 2.0:
                score += 2
                details.append(f"V={volume_ratio:.1f}x!!!")
            elif volume_ratio > 1.3:
                score += 1
                details.append(f"V={volume_ratio:.1f}x")

            if trend_15m == -1:
                score += 1
                details.append("15m BEAR")

            if bb_pct_15m is not None and bb_pct_15m > 0.75:
                score += 1
                details.append("15m BB high")

            vwap = row.get("vwap")
            if vwap is not None and close > vwap * 1.002:
                score += 1
                details.append(">VWAP")

            if score >= 5:
                signal = -1
                confidence = 2 if score >= 7 else 1
                trigger = "bb_upper_bounce"
                reason_parts = details

    if signal == 0:
        return no_signal

    return {
        "signal": signal,
        "reason": " | ".join(reason_parts),
        "confidence": confidence,
        "trigger": trigger,
    }
