"""기술적 지표 계산 모듈 - MA, RSI, BB, MTF, ADX."""

import numpy as np
import pandas as pd

pd.set_option('future.no_silent_downcasting', True)


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균(EMA) 계산."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """단순이동평균(SMA) 계산."""
    return series.rolling(window=period).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI 계산."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """ADX, +DI, -DI 계산.

    Returns:
        DataFrame with columns: adx, plus_di, minus_di
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = pd.Series(tr, index=df.index).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di_raw = pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    minus_di_raw = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = 100 * plus_di_raw / atr.replace(0, np.nan)
    minus_di = 100 * minus_di_raw / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    result = pd.DataFrame(index=df.index)
    result["adx"] = adx.fillna(0)
    result["plus_di"] = plus_di.fillna(0)
    result["minus_di"] = minus_di.fillna(0)
    return result


def calc_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """볼린저밴드 계산.

    Returns:
        DataFrame with: bb_upper, bb_mid, bb_lower, bb_pct, bb_width
    """
    close = df["close"]
    mid = sma(close, period)
    std = close.rolling(window=period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std

    band_range = upper - lower
    bb_pct = (close - lower) / band_range.replace(0, np.nan)
    bb_width = band_range / mid.replace(0, np.nan)

    result = pd.DataFrame(index=df.index)
    result["bb_upper"] = upper
    result["bb_mid"] = mid
    result["bb_lower"] = lower
    result["bb_pct"] = bb_pct.fillna(0.5)
    result["bb_width"] = bb_width.fillna(0)
    return result


def calc_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """모든 지표를 한 번에 계산하여 DataFrame에 추가.

    Input df must have: open, high, low, close, volume
    """
    df = df.copy()

    # EMA
    df["ema9"] = ema(df["close"], 9)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)

    # 4H EMA 근사 (1H에서 계산)
    df["ema20_4h"] = ema(df["close"], 80)   # 1H EMA80 ≈ 4H EMA20
    df["ema50_4h"] = ema(df["close"], 200)  # 1H EMA200 ≈ 4H EMA50

    # RSI
    df["rsi"] = calc_rsi(df["close"], 14)

    # ADX
    adx_df = calc_adx(df, 14)
    df["adx"] = adx_df["adx"]
    df["plus_di"] = adx_df["plus_di"]
    df["minus_di"] = adx_df["minus_di"]

    # 볼린저밴드
    bb_df = calc_bollinger(df, 20, 2.0)
    df["bb_upper"] = bb_df["bb_upper"]
    df["bb_mid"] = bb_df["bb_mid"]
    df["bb_lower"] = bb_df["bb_lower"]
    df["bb_pct"] = bb_df["bb_pct"]
    df["bb_width"] = bb_df["bb_width"]

    # 볼린저 스퀴즈: 밴드폭이 최근 50봉 중 하위 20%
    df["bb_width_pctile"] = df["bb_width"].rolling(window=50).rank(pct=True)
    df["is_squeeze"] = df["bb_width_pctile"] < 0.2

    # 거래량 비율 (20봉 평균 대비)
    vol_ma = sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / vol_ma.replace(0, np.nan)
    df["volume_ratio"] = df["volume_ratio"].fillna(1.0)

    # EMA20 크로스 감지 (상향/하향)
    df["ema20_above_50"] = df["ema20"] > df["ema50"]
    prev_above = df["ema20_above_50"].shift(1)
    df["ema20_cross_up"] = df["ema20_above_50"] & ~prev_above.fillna(False).astype(bool)
    # P0 fix: fillna(False) for both — symmetric behavior at start of data
    df["ema20_cross_down"] = ~df["ema20_above_50"] & prev_above.fillna(False).astype(bool)

    # RSI 방향 전환 감지
    rsi = df["rsi"]
    df["rsi_reversal_up"] = (rsi > rsi.shift(1)) & (rsi.shift(1) < rsi.shift(2))
    df["rsi_reversal_down"] = (rsi < rsi.shift(1)) & (rsi.shift(1) > rsi.shift(2))

    # 스퀴즈 해소: 이전 봉이 스퀴즈였고, 현재 봉은 아닌 경우
    df["squeeze_release"] = df["is_squeeze"].shift(1).fillna(False).astype(bool) & ~df["is_squeeze"]

    # 눌림목: close와 EMA20의 거리가 0.5% 이내
    df["pullback_to_ema20"] = ((df["close"] - df["ema20"]).abs() / df["ema20"]) < 0.005

    # 양봉/음봉
    df["is_bullish"] = df["close"] > df["open"]
    df["is_bearish"] = df["close"] < df["open"]

    return df
