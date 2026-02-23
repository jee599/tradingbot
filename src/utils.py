"""유틸리티 함수 모음."""

import time
from datetime import datetime, timezone


def timestamp_now() -> str:
    """현재 UTC ISO 타임스탬프."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def date_today() -> str:
    """오늘 날짜 (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def month_str() -> str:
    """현재 월 (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def generate_trade_id() -> str:
    """고유 매매 ID 생성."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    seq = int(time.time() * 1000) % 1000
    return f"T-{ts}-{seq:03d}"


def pct_change(entry: float, current: float, side: str) -> float:
    """진입가 대비 수익률(%) 계산.

    Args:
        entry: 진입가
        current: 현재가
        side: 'Buy' (롱) 또는 'Sell' (숏)
    """
    if entry == 0:
        return 0.0
    if side == "Buy":
        return ((current - entry) / entry) * 100
    else:
        return ((entry - current) / entry) * 100


def round_price(price: float, tick_size: float = 0.0001) -> float:
    """가격을 tick_size 단위로 반올림."""
    return round(round(price / tick_size) * tick_size, 6)


def round_qty(qty: float, step_size: float = 1.0) -> float:
    """수량을 step_size 단위로 내림."""
    return float(int(qty / step_size) * step_size)


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """안전한 나눗셈."""
    return a / b if b != 0 else default


def seconds_until_next_hour() -> int:
    """다음 정시까지 남은 초."""
    now = datetime.now(timezone.utc)
    return 3600 - (now.minute * 60 + now.second)
