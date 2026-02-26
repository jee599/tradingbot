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
    # 캔들 봉 (Bybit interval).
    # Bybit linear kline은 10분봉("10")을 지원하지 않는다(응답 OK지만 list가 비어있음).
    # 그래서 스캘핑 기본은 15분봉으로 맵핑한다.
    _INTERVAL_RAW: str = os.getenv("INTERVAL", "60")
    INTERVAL: str = "15" if _INTERVAL_RAW == "10" else _INTERVAL_RAW

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

    # ──────────────────────────────────────────
    # 스캘핑 전략 (Plan B)
    # ──────────────────────────────────────────
    SCALP_MODE: bool = os.getenv("SCALP_MODE", "false").lower() == "true"

    # 타임프레임
    SCALP_ENTRY_INTERVAL: str = os.getenv("SCALP_ENTRY_INTERVAL", "5")    # 5분봉 진입
    SCALP_FILTER_INTERVAL: str = os.getenv("SCALP_FILTER_INTERVAL", "15")  # 15분봉 필터

    # 스캘핑 SL/TP (기본 전략보다 타이트)
    SCALP_STOP_LOSS_PCT: float = float(os.getenv("SCALP_STOP_LOSS_PCT", "0.8"))
    SCALP_TAKE_PROFIT_PCT: float = float(os.getenv("SCALP_TAKE_PROFIT_PCT", "1.4"))

    # 스캘핑 트레일링
    SCALP_TRAILING_ACTIVATE_PCT: float = float(os.getenv("SCALP_TRAILING_ACTIVATE_PCT", "0.8"))
    SCALP_TRAILING_CALLBACK_PCT: float = float(os.getenv("SCALP_TRAILING_CALLBACK_PCT", "0.4"))

    # 스캘핑 시간 청산 (분 단위)
    SCALP_TIME_EXIT_MINUTES: int = int(os.getenv("SCALP_TIME_EXIT_MINUTES", "45"))

    # 스캘핑 시그널 주기 (초)
    SCALP_SIGNAL_INTERVAL_SEC: int = int(os.getenv("SCALP_SIGNAL_INTERVAL_SEC", "60"))

    # 15m 필터 EMA
    SCALP_FILTER_EMA_FAST: int = int(os.getenv("SCALP_FILTER_EMA_FAST", "50"))
    SCALP_FILTER_EMA_SLOW: int = int(os.getenv("SCALP_FILTER_EMA_SLOW", "200"))

    # 5m 풀백 트리거: price-to-EMA20 거리 %
    SCALP_PULLBACK_DIST_PCT: float = float(os.getenv("SCALP_PULLBACK_DIST_PCT", "0.3"))
    # 5m 풀백 RSI 범위
    SCALP_PULLBACK_RSI_LOW: float = float(os.getenv("SCALP_PULLBACK_RSI_LOW", "35"))
    SCALP_PULLBACK_RSI_HIGH: float = float(os.getenv("SCALP_PULLBACK_RSI_HIGH", "65"))

    # 5m BB 브레이크아웃 볼륨 배수
    SCALP_BB_VOL_RATIO: float = float(os.getenv("SCALP_BB_VOL_RATIO", "1.5"))

    # 스캘핑 레짐 필터 (횡보장 회피)
    SCALP_REGIME_FILTER: bool = os.getenv("SCALP_REGIME_FILTER", "true").lower() == "true"
    SCALP_REGIME_ADX_MIN: float = float(os.getenv("SCALP_REGIME_ADX_MIN", "20"))
    SCALP_REGIME_BB_WIDTH_MIN: float = float(os.getenv("SCALP_REGIME_BB_WIDTH_MIN", "0.005"))

    # 스캘핑 수수료+슬리피지 버퍼 (SL/TP 보정)
    # 기본값: 왕복 taker 0.055%×2 = 0.11%, 슬리피지 추가 → 0.15%
    SCALP_FEE_BUFFER_PCT: float = float(os.getenv("SCALP_FEE_BUFFER_PCT", "0.15"))

    # 스캘핑 브레이크이븐 시간 청산 (분)
    # 일정 시간 후 수익이 fee buffer 미만이면 조기 청산
    SCALP_TIME_EXIT_BREAKEVEN_MIN: int = int(os.getenv("SCALP_TIME_EXIT_BREAKEVEN_MIN", "30"))

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
