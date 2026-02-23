"""전략 모듈 - MA+RSI+BB+MTF 4지표 과반수 투표."""

from __future__ import annotations

import logging
import pandas as pd

logger = logging.getLogger("xrp_bot")


def signal_ma(row: pd.Series) -> tuple[int, str]:
    """MA (이동평균) 시그널.

    - 롱: EMA20 > EMA50 상향 교차 + ADX > 20
    - 숏: EMA20 < EMA50 하향 교차 + ADX > 20
    """
    adx = row.get("adx", 0)
    if adx < 20:
        return 0, f"ADX={adx:.1f}<20, no trend"

    if row.get("ema20_cross_up", False):
        return 1, f"EMA20 crossed above EMA50, ADX={adx:.1f}>20"
    if row.get("ema20_cross_down", False):
        return -1, f"EMA20 crossed below EMA50, ADX={adx:.1f}>20"

    # 크로스는 없지만 추세 유지 중이면 기존 방향 유지
    if row.get("ema20_above_50", False) and adx > 25:
        return 1, f"EMA20>EMA50 trend continues, ADX={adx:.1f}"
    if not row.get("ema20_above_50", True) and adx > 25:
        return -1, f"EMA20<EMA50 trend continues, ADX={adx:.1f}"

    return 0, f"No MA crossover, ADX={adx:.1f}"


def signal_rsi(row: pd.Series) -> tuple[int, str]:
    """RSI 시그널.

    - 롱: RSI < 35에서 반등 감지
    - 숏: RSI > 65에서 하락 감지
    - 40~60 구간은 중립
    """
    rsi = row.get("rsi", 50)

    if rsi < 35 and row.get("rsi_reversal_up", False):
        return 1, f"RSI={rsi:.1f}<35, reversal up detected"
    if rsi > 65 and row.get("rsi_reversal_down", False):
        return -1, f"RSI={rsi:.1f}>65, reversal down detected"
    if 40 <= rsi <= 60:
        return 0, f"RSI={rsi:.1f}, neutral zone (40-60)"

    return 0, f"RSI={rsi:.1f}, no reversal signal"


def signal_bb(row: pd.Series) -> tuple[int, str]:
    """볼린저밴드 시그널.

    - 스퀴즈 해소: close > middle → 롱, close < middle → 숏
    - 비스퀴즈: bb_pct < 0.05 + 거래량 > 1.0 → 롱
    - 비스퀴즈: bb_pct > 0.95 + 거래량 > 1.0 → 숏
    """
    bb_pct = row.get("bb_pct", 0.5)
    close = row.get("close", 0)
    bb_mid = row.get("bb_mid", 0)
    vol_ratio = row.get("volume_ratio", 1.0)
    squeeze_release = row.get("squeeze_release", False)

    if squeeze_release:
        if close > bb_mid:
            return 1, f"Squeeze release, close>{bb_mid:.4f}(mid) → long"
        else:
            return -1, f"Squeeze release, close<{bb_mid:.4f}(mid) → short"

    if bb_pct < 0.05 and vol_ratio > 1.0:
        return 1, f"bb_pct={bb_pct:.2f}<0.05, vol_ratio={vol_ratio:.1f}>1.0 → long"
    if bb_pct > 0.95 and vol_ratio > 1.0:
        return -1, f"bb_pct={bb_pct:.2f}>0.95, vol_ratio={vol_ratio:.1f}>1.0 → short"

    return 0, f"bb_pct={bb_pct:.2f}, no BB signal"


def signal_mtf(row: pd.Series) -> tuple[int, str]:
    """멀티타임프레임 시그널.

    - 롱: 4H 상승추세 + 1H 눌림목 + 양봉 + RSI < 55
    - 숏: 4H 하락추세 + 1H 눌림목 + 음봉 + RSI > 45
    """
    ema20_4h = row.get("ema20_4h", 0)
    ema50_4h = row.get("ema50_4h", 0)
    pullback = row.get("pullback_to_ema20", False)
    is_bullish = row.get("is_bullish", False)
    is_bearish = row.get("is_bearish", False)
    rsi = row.get("rsi", 50)

    uptrend_4h = ema20_4h > ema50_4h
    downtrend_4h = ema20_4h < ema50_4h

    if uptrend_4h and pullback and is_bullish and rsi < 55:
        return 1, (f"4H uptrend (ema20_4h>{ema50_4h:.4f}), "
                   f"pullback to 1H EMA20, bullish candle, RSI={rsi:.1f}<55")
    if downtrend_4h and pullback and is_bearish and rsi > 45:
        return -1, (f"4H downtrend (ema20_4h<{ema50_4h:.4f}), "
                    f"pullback to 1H EMA20, bearish candle, RSI={rsi:.1f}>45")

    return 0, f"MTF no signal (4H trend: {'up' if uptrend_4h else 'down'}, pullback={pullback})"


def generate_signals(df: pd.DataFrame) -> dict:
    """최신 봉 기준 4지표 시그널 생성 + 과반수 투표.

    Returns:
        {
            "MA": {"value": int, "reason": str},
            "RSI": {"value": int, "reason": str},
            "BB": {"value": int, "reason": str},
            "MTF": {"value": int, "reason": str},
            "combined_signal": int,
            "signal_detail": str,
            "buy_count": int,
            "sell_count": int,
            "confidence": int,
        }
    """
    if df.empty or len(df) < 200:
        return {
            "MA": {"value": 0, "reason": "Insufficient data"},
            "RSI": {"value": 0, "reason": "Insufficient data"},
            "BB": {"value": 0, "reason": "Insufficient data"},
            "MTF": {"value": 0, "reason": "Insufficient data"},
            "combined_signal": 0,
            "signal_detail": "Insufficient data",
            "buy_count": 0,
            "sell_count": 0,
            "confidence": 0,
        }

    row = df.iloc[-1]

    ma_val, ma_reason = signal_ma(row)
    rsi_val, rsi_reason = signal_rsi(row)
    bb_val, bb_reason = signal_bb(row)
    mtf_val, mtf_reason = signal_mtf(row)

    values = [ma_val, rsi_val, bb_val, mtf_val]
    buy_count = sum(1 for v in values if v == 1)
    sell_count = sum(1 for v in values if v == -1)

    # 과반수 투표
    if buy_count >= 2 and sell_count == 0:
        combined = 1
        detail = f"{buy_count}/4 long, {sell_count}/4 short → LONG (confidence: {buy_count})"
    elif sell_count >= 2 and buy_count == 0:
        combined = -1
        detail = f"{buy_count}/4 long, {sell_count}/4 short → SHORT (confidence: {sell_count})"
    else:
        combined = 0
        detail = f"{buy_count}/4 long, {sell_count}/4 short → NO SIGNAL"

    confidence = max(buy_count, sell_count)

    result = {
        "MA": {"value": ma_val, "reason": ma_reason},
        "RSI": {"value": rsi_val, "reason": rsi_reason},
        "BB": {"value": bb_val, "reason": bb_reason},
        "MTF": {"value": mtf_val, "reason": mtf_reason},
        "combined_signal": combined,
        "signal_detail": detail,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "confidence": confidence,
    }

    logger.debug(f"SIGNAL: {detail}")
    return result
