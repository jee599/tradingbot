"""설정 관리 모듈 - .env 로드 및 전역 설정."""

from __future__ import annotations

import os
from enum import Enum
from dotenv import load_dotenv


class PositionMode(Enum):
    """Bybit 포지션 모드."""
    ONE_WAY = 0   # 단방향 (positionIdx=0)
    HEDGE = 1     # 양방향 (positionIdx=1 Buy / 2 Sell)

load_dotenv()


class Config:
    """환경변수 기반 설정."""

    # Bybit API
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_API_SECRET: str = os.getenv("BYBIT_API_SECRET", "")
    BYBIT_TESTNET: bool = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

    # 텔레그램
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # 전략 파라미터
    SYMBOL: str = os.getenv("SYMBOL", "XRPUSDT")  # 하위 호환
    SYMBOLS: list = [s.strip() for s in os.getenv("SYMBOLS", os.getenv("SYMBOL", "XRPUSDT")).split(",") if s.strip()]
    CATEGORY: str = "linear"
    INTERVAL: str = "60"  # 1시간봉
    KLINE_LIMIT: int = 300

    LEVERAGE: int = int(os.getenv("LEVERAGE", "1"))
    POSITION_SIZE_PCT: float = float(os.getenv("POSITION_SIZE_PCT", "5"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "2.0"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "4.0"))
    TRAILING_STOP_ACTIVATE_PCT: float = float(os.getenv("TRAILING_STOP_ACTIVATE_PCT", "2.0"))
    TRAILING_STOP_CALLBACK_PCT: float = float(os.getenv("TRAILING_STOP_CALLBACK_PCT", "1.0"))

    # 포지션 사이징
    HIGH_CONFIDENCE_SIZE_PCT: float = 8.0
    MAX_POSITION_SIZE_PCT: float = 10.0
    HIGH_CONFIDENCE_THRESHOLD: int = 3  # 3개 이상 지표 동의

    # 리스크 관리
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
    MAX_DAILY_TRADES: int = int(os.getenv("MAX_DAILY_TRADES", "5"))
    COOLDOWN_AFTER_SL_STREAK: int = int(os.getenv("COOLDOWN_AFTER_SL_STREAK", "3"))
    COOLDOWN_HOURS: int = int(os.getenv("COOLDOWN_HOURS", "2"))
    MAX_LEVERAGE: int = 5

    # 청산 규칙
    TIME_EXIT_HOURS: int = 48

    # 진입 필터
    MIN_VOLUME_RATIO: float = float(os.getenv("MIN_VOLUME_RATIO", "0.3"))  # 20봉 평균 대비
    MAX_SPREAD_MULTIPLIER: float = 3.0  # 평소 대비 3배 이상 스프레드
    RECENT_SL_LOOKBACK: int = 3  # 최근 3봉 내 손절

    # 로그
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG")

    @classmethod
    def validate(cls) -> list[str]:
        """필수 설정값 검증. 누락 항목 리스트 반환."""
        errors = []
        if not cls.BYBIT_API_KEY:
            errors.append("BYBIT_API_KEY가 설정되지 않았습니다.")
        if not cls.BYBIT_API_SECRET:
            errors.append("BYBIT_API_SECRET이 설정되지 않았습니다.")
        if cls.LEVERAGE > cls.MAX_LEVERAGE:
            errors.append(f"LEVERAGE({cls.LEVERAGE})가 최대값({cls.MAX_LEVERAGE})을 초과합니다.")
        if cls.POSITION_SIZE_PCT > cls.MAX_POSITION_SIZE_PCT:
            errors.append(f"POSITION_SIZE_PCT({cls.POSITION_SIZE_PCT})가 최대값({cls.MAX_POSITION_SIZE_PCT})을 초과합니다.")
        return errors

    @classmethod
    def set_testnet(cls, enabled: bool):
        """테스트넷 모드 전환."""
        cls.BYBIT_TESTNET = enabled
