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

    # 수익 극대화 모드(B): 트레일링을 너무 일찍 켜면 승자가 잘려서 win small이 되기 쉬움.
    # 기본값은 "늦게 활성 + 넓은 콜백"으로 설정.
    ENABLE_TRAILING_STOP: bool = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
    TRAILING_STOP_ACTIVATE_PCT: float = float(os.getenv("TRAILING_STOP_ACTIVATE_PCT", "3.5"))
    TRAILING_STOP_CALLBACK_PCT: float = float(os.getenv("TRAILING_STOP_CALLBACK_PCT", "2.0"))

    # 엔트리 품질
    MIN_ENTRY_CONFIDENCE: int = int(os.getenv("MIN_ENTRY_CONFIDENCE", "3"))

    # 캔들 마감 기준으로만 신규 진입 판단 (과매매/잡음 감소)
    TRADE_ON_CANDLE_CLOSE_ONLY: bool = os.getenv("TRADE_ON_CANDLE_CLOSE_ONLY", "true").lower() == "true"

    # 포지션 사이징 (잔고 기반)
    BALANCE_UTILIZATION_PCT: float = float(os.getenv("BALANCE_UTILIZATION_PCT", "90"))
    MIN_RESERVE_USDT: float = float(os.getenv("MIN_RESERVE_USDT", "10"))
    SAFETY_HAIRCUT_PCT: float = float(os.getenv("SAFETY_HAIRCUT_PCT", "5"))
    HIGH_CONFIDENCE_SIZE_PCT: float = 8.0
    MAX_POSITION_SIZE_PCT: float = float(os.getenv("MAX_POSITION_SIZE_PCT", "10.0"))
    HIGH_CONFIDENCE_THRESHOLD: int = 3  # 3개 이상 지표 동의

    # 전체 노출 한도 (멀티심볼 합산 마진 / equity %)
    MAX_TOTAL_EXPOSURE_PCT: float = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "30.0"))

    # 리스크 관리
    MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
    ENFORCE_DAILY_LOSS_LIMIT: bool = os.getenv("ENFORCE_DAILY_LOSS_LIMIT", "true").lower() == "true"
    MAX_DAILY_TRADES: int = int(os.getenv("MAX_DAILY_TRADES", "5"))

    # 동시 오픈 포지션 최대 개수
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))

    # 피라미딩(추가진입)
    PYRAMID_MAX_ADDS: int = int(os.getenv("PYRAMID_MAX_ADDS", "2"))
    PYRAMID_ADD_SIZE_MULT: float = float(os.getenv("PYRAMID_ADD_SIZE_MULT", "0.5"))  # 초기 사이즈 대비
    PYRAMID_MIN_PROFIT_PCT: float = float(os.getenv("PYRAMID_MIN_PROFIT_PCT", "0.3"))
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
