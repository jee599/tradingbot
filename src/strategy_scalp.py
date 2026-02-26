"""스캘핑 전략 모듈 (Plan B) - 15m 추세필터 + 5m 트리거."""

from __future__ import annotations

import logging
import pandas as pd

from src.config import Config
from src.indicators import ema, calc_rsi, calc_bollinger, sma

logger = logging.getLogger("xrp_bot")


# ──────────────────────────────────────────
# 15m 추세 필터
# ──────────────────────────────────────────

def calc_trend_filter(df_15m: pd.DataFrame) -> int:
    """15분봉 추세 방향 판별.

    EMA50 > EMA200 → +1 (long only)
    EMA50 < EMA200 → -1 (short only)
    데이터 부족     →  0 (no trade)

    Args:
        df_15m: 15m OHLCV DataFrame (최소 200봉 필요).

    Returns:
        +1 (long bias), -1 (short bias), 0 (neutral/insufficient data)
    """
    fast_period = Config.SCALP_FILTER_EMA_FAST
    slow_period = Config.SCALP_FILTER_EMA_SLOW

    if df_15m.empty or len(df_15m) < slow_period:
        return 0

    close = df_15m["close"]
    ema_fast = ema(close, fast_period).iloc[-1]
    ema_slow = ema(close, slow_period).iloc[-1]

    if ema_fast > ema_slow:
        return 1
    elif ema_fast < ema_slow:
        return -1
    return 0


# ──────────────────────────────────────────
# 5m 지표 계산
# ──────────────────────────────────────────

def calc_scalp_indicators(df_5m: pd.DataFrame) -> pd.DataFrame:
    """5분봉 지표 계산 (스캘핑용).

    추가 컬럼:
        ema20, rsi, bb_upper, bb_mid, bb_lower, bb_pct,
        volume_ratio, is_bullish, is_bearish,
        pullback_to_ema20, bb_breakout_up, bb_breakout_down
    """
    df = df_5m.copy()

    df["ema20"] = ema(df["close"], 20)
    df["rsi"] = calc_rsi(df["close"], 14)

    bb = calc_bollinger(df, 20, 2.0)
    df["bb_upper"] = bb["bb_upper"]
    df["bb_mid"] = bb["bb_mid"]
    df["bb_lower"] = bb["bb_lower"]
    df["bb_pct"] = bb["bb_pct"]

    vol_ma = sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / vol_ma.replace(0, float("nan"))
    df["volume_ratio"] = df["volume_ratio"].fillna(1.0)

    df["is_bullish"] = df["close"] > df["open"]
    df["is_bearish"] = df["close"] < df["open"]

    # Pullback: price near EMA20
    dist_pct = Config.SCALP_PULLBACK_DIST_PCT / 100
    df["pullback_to_ema20"] = ((df["close"] - df["ema20"]).abs() / df["ema20"]) < dist_pct

    # BB breakout
    df["bb_breakout_up"] = df["close"] > df["bb_upper"]
    df["bb_breakout_down"] = df["close"] < df["bb_lower"]

    return df


# ──────────────────────────────────────────
# 5m 트리거: Pullback
# ──────────────────────────────────────────

def signal_pullback(row: pd.Series, trend: int) -> tuple[int, str]:
    """풀백 트리거.

    Long: trend==+1, price near EMA20, reclaim (bullish candle), RSI in band
    Short: trend==-1, price near EMA20, reject (bearish candle), RSI in band
    """
    if trend == 0:
        return 0, "No trend → no pullback signal"

    pullback = row.get("pullback_to_ema20", False)
    if not pullback:
        return 0, "Price not near EMA20"

    rsi = row.get("rsi", 50)
    rsi_low = Config.SCALP_PULLBACK_RSI_LOW
    rsi_high = Config.SCALP_PULLBACK_RSI_HIGH
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)

    if trend == 1:
        if is_bullish and rsi_low <= rsi <= rsi_high:
            return 1, f"Pullback long: near EMA20, bullish candle, RSI={rsi:.1f}"
        return 0, f"Pullback miss: bullish={is_bullish}, RSI={rsi:.1f}"

    if trend == -1:
        if is_bearish and rsi_low <= rsi <= rsi_high:
            return -1, f"Pullback short: near EMA20, bearish candle, RSI={rsi:.1f}"
        return 0, f"Pullback miss: bearish={is_bearish}, RSI={rsi:.1f}"

    return 0, "Pullback: unexpected"


# ──────────────────────────────────────────
# 5m 트리거: BB Breakout
# ──────────────────────────────────────────

def signal_breakout(row: pd.Series, trend: int) -> tuple[int, str]:
    """BB 브레이크아웃 트리거.

    Long: trend==+1, close > BB upper, volume_ratio > threshold
    Short: trend==-1, close < BB lower, volume_ratio > threshold
    """
    if trend == 0:
        return 0, "No trend → no breakout signal"

    vol_ratio = row.get("volume_ratio", 1.0)
    vol_threshold = Config.SCALP_BB_VOL_RATIO

    if trend == 1 and row.get("bb_breakout_up", False) and vol_ratio >= vol_threshold:
        return 1, f"BB breakout long: close > upper, vol={vol_ratio:.1f}x"
    if trend == -1 and row.get("bb_breakout_down", False) and vol_ratio >= vol_threshold:
        return -1, f"BB breakout short: close < lower, vol={vol_ratio:.1f}x"

    return 0, f"No breakout (trend={trend}, vol={vol_ratio:.1f}x)"


# ──────────────────────────────────────────
# 통합 시그널
# ──────────────────────────────────────────

def generate_scalp_signals(
    df_5m: pd.DataFrame,
    df_15m: pd.DataFrame,
) -> dict:
    """스캘핑 시그널 생성.

    1. 15m 추세 필터로 방향 결정
    2. 5m에서 pullback 또는 breakout 트리거 확인
    3. 트리거 중 하나라도 발동하면 진입 시그널

    Returns:
        {
            "trend_filter": int,           # +1/-1/0
            "trend_reason": str,
            "pullback": {"value": int, "reason": str},
            "breakout": {"value": int, "reason": str},
            "combined_signal": int,        # +1/-1/0
            "signal_detail": str,
            "confidence": int,             # 0, 1, 2 (both triggers agree)
            "trigger": str,                # "pullback" / "breakout" / "both" / "none"
        }
    """
    empty_result = {
        "trend_filter": 0,
        "trend_reason": "Insufficient data",
        "pullback": {"value": 0, "reason": "Insufficient data"},
        "breakout": {"value": 0, "reason": "Insufficient data"},
        "combined_signal": 0,
        "signal_detail": "Insufficient data",
        "confidence": 0,
        "trigger": "none",
    }

    if df_5m.empty or len(df_5m) < 50:
        return empty_result

    # 1. 15m 추세 필터
    trend = calc_trend_filter(df_15m)
    if trend == 1:
        trend_reason = "15m EMA50 > EMA200 → long bias"
    elif trend == -1:
        trend_reason = "15m EMA50 < EMA200 → short bias"
    else:
        trend_reason = "15m no trend or insufficient data"

    # 2. 5m 지표 계산
    df_5m = calc_scalp_indicators(df_5m)
    row = df_5m.iloc[-1]

    # 3. 트리거 체크
    pb_val, pb_reason = signal_pullback(row, trend)
    bo_val, bo_reason = signal_breakout(row, trend)

    # 4. 통합: 어느 하나라도 발동하면 진입
    combined = 0
    trigger = "none"
    triggers_fired = 0

    if pb_val != 0:
        combined = pb_val
        trigger = "pullback"
        triggers_fired += 1
    if bo_val != 0:
        combined = bo_val
        trigger = "breakout" if triggers_fired == 0 else "both"
        triggers_fired += 1

    confidence = triggers_fired  # 0, 1, or 2

    if combined == 1:
        detail = f"SCALP LONG ({trigger}) | 15m: {trend_reason}"
    elif combined == -1:
        detail = f"SCALP SHORT ({trigger}) | 15m: {trend_reason}"
    else:
        detail = f"SCALP NO SIGNAL | 15m: {trend_reason}"

    result = {
        "trend_filter": trend,
        "trend_reason": trend_reason,
        "pullback": {"value": pb_val, "reason": pb_reason},
        "breakout": {"value": bo_val, "reason": bo_reason},
        "combined_signal": combined,
        "signal_detail": detail,
        "confidence": confidence,
        "trigger": trigger,
    }

    logger.debug(f"SCALP_SIGNAL: {detail}")
    return result
