# XRP 자동매매 봇

## 프로젝트 개요
Bybit V5 API를 사용한 XRP/USDT 무기한선물 자동매매 봇.
백테스트 결과 MA+RSI+BB+MTF 4지표 조합이 최적 (승률 67%, PF 2.97, MDD -2.9%).

## 환경
- Python 3.9+
- Bybit V5 API (pybit 라이브러리)
- XRP/USDT 무기한선물 (category: linear)
- 1시간봉 기준 (4시간봉은 내부 계산)
- 아이맥 로컬에서 24시간 운영
- .env 파일로 시크릿 관리
- --testnet 플래그로 테스트넷/실전 전환

## 의존성
```
pybit>=5.10.0
pandas
numpy
python-dotenv
requests  # 텔레그램
schedule  # 스케줄링
```

---

## 전략: MA + RSI + BB + MTF (4지표 과반수 투표)

### 지표 계산

#### 1. MA (이동평균)
- EMA 9, 20, 50, 200 계산
- ADX(14) 계산 (추세 강도)
- 시그널 조건:
  - 롱: EMA20 > EMA50 상향 교차 + ADX > 20
  - 숏: EMA20 < EMA50 하향 교차 + ADX > 20
  - ADX < 20이면 추세 없음 → 시그널 무시

#### 2. RSI
- RSI(14) 계산
- 방향 전환 감지: 현재 RSI > 직전 RSI이고, 직전 RSI < 그 직전 RSI
- 시그널 조건:
  - 롱: RSI < 35에서 반등(방향 전환) 감지
  - 숏: RSI > 65에서 하락(방향 전환) 감지
  - RSI 40~60 구간은 중립 → 시그널 없음

#### 3. BB (볼린저밴드)
- 20기간 SMA, 표준편차 × 2
- bb_pct = (close - lower) / (upper - lower)
- 밴드폭(bandwidth) = (upper - lower) / middle
- 스퀴즈 감지: 밴드폭이 최근 50봉 중 하위 20%
- 시그널 조건:
  - 스퀴즈 해소 시: close > middle → 롱, close < middle → 숏
  - 비스퀴즈 구간: bb_pct < 0.05 + 거래량 비율 > 1.0 → 롱
  - 비스퀴즈 구간: bb_pct > 0.95 + 거래량 비율 > 1.0 → 숏

#### 4. MTF (멀티타임프레임)
- 4시간 EMA 근사: EMA(80) = 1H EMA20의 4H 등가, EMA(200) = 1H EMA50의 4H 등가
- 눌림목 감지: close와 EMA20의 거리가 0.5% 이내
- 시그널 조건:
  - 롱: 4H 상승추세 (ema20_4h > ema50_4h) + 1H 눌림목 + 양봉 + RSI < 55
  - 숏: 4H 하락추세 (ema20_4h < ema50_4h) + 1H 눌림목 + 음봉 + RSI > 45

### 시그널 조합 규칙 (과반수 투표)

```
4개 지표 각각 +1(롱), 0(중립), -1(숏) 시그널 생성

매수(롱) 조건:
  - 2개 이상 롱 시그널 (buy_count >= 2)
  - 숏 시그널 0개 (sell_count == 0)

매도(숏) 조건:
  - 2개 이상 숏 시그널 (sell_count >= 2)
  - 롱 시그널 0개 (buy_count == 0)

그 외: 시그널 없음 → 현재 상태 유지
```

### 진입 필터 (추가 안전장치)

아래 조건 중 하나라도 해당하면 **진입 금지**:
- 최근 3봉 내 손절 발생 (연속 손절 방지)
- 거래량이 20봉 평균의 30% 미만 (유동성 부족)
- 스프레드가 평소의 3배 이상 (비정상 시장)
- 이미 포지션 보유 중 (중복 진입 금지)

### 포지션 사이징

```
기본: 자본의 5%
확신도(3개 이상 지표 동의): 자본의 8%
최대: 절대 10% 초과 금지
레버리지: 3x (처음에는 1x로 시작, 안정화 후 3x)

예시: 자본 500만원, 3x 레버리지
  기본 진입: 500만 × 5% = 25만원 × 3x = 75만원 포지션
  확신 진입: 500만 × 8% = 40만원 × 3x = 120만원 포지션
```

### 청산 규칙

```
1. 손절 (Stop Loss): -2.0%
   → 즉시 시장가 청산
   → 로그에 "SL_HIT" 기록

2. 익절 (Take Profit): +4.0%
   → 즉시 시장가 청산
   → 로그에 "TP_HIT" 기록

3. 트레일링 스탑: +2.0% 도달 후 활성화
   → 고점 대비 -1.0% 되돌림 시 청산
   → 로그에 "TRAILING_STOP" 기록

4. 시그널 반전 청산:
   → 보유 방향과 반대 시그널 발생 시 즉시 청산
   → 로그에 "SIGNAL_REVERSE" 기록

5. 시간 기반 청산:
   → 진입 후 48시간 경과 + 수익 0% 미만 → 청산
   → 로그에 "TIME_EXIT" 기록
```

---

## 실행 루프

```python
"""
메인 루프 (1시간 캔들 완성 시점 기준):

매 시간 정각 + 10초 (캔들 확정 대기):
  1. Bybit에서 최근 300봉 OHLCV 조회
  2. 지표 계산 (MA, RSI, BB, MTF, ADX)
  3. 각 지표별 시그널 생성
  4. 과반수 투표로 최종 시그널 결정
  5. 진입 필터 체크
  6. 포지션 있으면 → 청산 조건 체크
  7. 포지션 없으면 → 시그널에 따라 진입
  8. 로그 기록 + 텔레그램 알림

추가 루프 (10초마다):
  - 포지션 보유 중이면 현재가 체크
  - 손절/익절/트레일링 실시간 모니터링
  - 급변동 시 즉시 청산
"""
```

---

## 로그 시스템 (매우 상세하게)

### 로그 파일 구조

```
logs/
├── bot.log                      # 메인 로그 (INFO 이상)
├── bot_debug.log                # 디버그 포함 전체 로그
├── trades/
│   ├── trades_2025-02.json      # 월별 매매 기록
│   └── trades_2025-03.json
├── signals/
│   ├── signals_2025-02-23.json  # 일별 시그널 기록
│   └── ...
├── equity/
│   ├── equity_2025-02-23.csv    # 일별 잔고 추이
│   └── ...
└── errors/
    └── errors.log               # 에러 전용 로그
```

### 매매 로그 (trades JSON)

모든 매매에 대해 아래 필드 기록:

```json
{
  "trade_id": "T-20250223-143000-001",
  "timestamp_open": "2025-02-23T14:30:10.123Z",
  "timestamp_close": "2025-02-23T18:30:05.456Z",
  "symbol": "XRPUSDT",
  "side": "Buy",
  "direction": "Long",
  "entry_price": 2.3450,
  "exit_price": 2.4388,
  "quantity": 100,
  "leverage": 3,
  "position_value_usdt": 234.50,
  "margin_used_usdt": 78.17,
  
  "pnl_usdt": 9.38,
  "pnl_pct": 4.00,
  "fee_entry_usdt": 0.23,
  "fee_exit_usdt": 0.24,
  "fee_total_usdt": 0.47,
  "net_pnl_usdt": 8.91,
  "net_pnl_pct": 3.80,
  
  "exit_reason": "TP_HIT",
  "holding_hours": 4.0,
  
  "signals_at_entry": {
    "MA": 1,
    "RSI": 1,
    "BB": 0,
    "MTF": 1,
    "combined": 1,
    "confidence": 3
  },
  
  "indicators_at_entry": {
    "ema20": 2.3380,
    "ema50": 2.3200,
    "rsi": 33.5,
    "bb_pct": 0.08,
    "bb_width": 0.032,
    "adx": 28.4,
    "ema20_4h": 2.3100,
    "ema50_4h": 2.2900,
    "volume_ratio": 1.8
  },
  
  "indicators_at_exit": {
    "ema20": 2.3520,
    "ema50": 2.3220,
    "rsi": 62.1,
    "bb_pct": 0.82
  },
  
  "market_context": {
    "price_change_24h_pct": -2.3,
    "volume_24h_usdt": 15000000,
    "funding_rate": 0.0001,
    "open_interest_change_pct": 5.2
  },
  
  "risk_metrics": {
    "max_drawdown_during_trade_pct": -0.8,
    "max_profit_during_trade_pct": 4.2,
    "time_to_max_profit_hours": 3.2,
    "trailing_stop_activated": true,
    "trailing_stop_high": 2.4430
  }
}
```

### 시그널 로그 (매 시간마다)

매 캔들 완성 시 시그널 계산 결과를 기록 (매매 안 해도 기록):

```json
{
  "timestamp": "2025-02-23T14:00:00Z",
  "candle": {
    "open": 2.3400, "high": 2.3520, "low": 2.3350, "close": 2.3450,
    "volume": 850000
  },
  "indicators": {
    "ema9": 2.3420, "ema20": 2.3380, "ema50": 2.3200, "ema200": 2.2800,
    "rsi": 33.5,
    "macd": -0.0012, "macd_signal": -0.0018, "macd_hist": 0.0006,
    "bb_upper": 2.3800, "bb_mid": 2.3500, "bb_lower": 2.3200,
    "bb_pct": 0.42, "bb_width": 0.026,
    "adx": 28.4, "plus_di": 22.1, "minus_di": 15.3,
    "ema20_4h": 2.3100, "ema50_4h": 2.2900,
    "volume_ratio": 1.8
  },
  "signals": {
    "MA": {"value": 1, "reason": "EMA20 crossed above EMA50, ADX=28.4>20"},
    "RSI": {"value": 1, "reason": "RSI=33.5<35, reversal detected (prev=31.2→32.8→33.5)"},
    "BB": {"value": 0, "reason": "bb_pct=0.42, mid-band, no signal"},
    "MTF": {"value": 1, "reason": "4H uptrend (ema20_4h>ema50_4h), pullback to 1H EMA20, bullish candle"}
  },
  "combined_signal": 1,
  "signal_detail": "3/4 long, 0/4 short → LONG (confidence: 3)",
  "filter_check": {
    "recent_sl": false,
    "low_volume": false,
    "wide_spread": false,
    "already_in_position": false,
    "passed": true
  },
  "action": "OPEN_LONG",
  "current_position": {
    "side": "Buy",
    "size": 100,
    "entry_price": 2.3450,
    "unrealized_pnl": 0,
    "unrealized_pnl_pct": 0
  }
}
```

### 잔고/자산 로그 (매 시간)

```csv
timestamp,total_equity,available_balance,position_margin,unrealized_pnl,realized_pnl_today,cumulative_pnl,drawdown_from_peak,num_trades_today,win_rate_7d
2025-02-23T14:00:00,1000.00,921.83,78.17,0.00,0.00,0.00,0.00,0,0.0
2025-02-23T15:00:00,1002.50,921.83,78.17,2.50,0.00,0.00,0.00,0,0.0
```

### 에러 로그

```
[2025-02-23 14:30:15] [ERROR] API_ERROR: Bybit returned 10004 (sign error) - Retrying 1/3
[2025-02-23 14:30:16] [ERROR] API_ERROR: Retry 1 succeeded
[2025-02-23 14:30:15] [CRITICAL] ORDER_FAILED: Buy 100 XRPUSDT at market failed - insufficient balance
[2025-02-23 14:30:15] [WARNING] RATE_LIMIT: 580/600 requests used, throttling
```

### 일일 서머리 (매일 00:00 UTC 자동 생성 + 텔레그램 발송)

```
📊 일일 리포트 | 2025-02-23
━━━━━━━━━━━━━━━━━━━━━━
💰 총 자산: $1,045.20 (+4.52%)
📈 오늘 실현 손익: +$12.30
📊 미실현 손익: +$3.50

🔄 오늘 매매: 3회 (2승 1패)
  ✅ Long +4.0% (TP) | 보유 4.0h
  ✅ Short +2.8% (Signal) | 보유 2.5h
  ❌ Long -2.0% (SL) | 보유 1.2h

📉 현재 포지션: Long 100 XRP @ $2.3450
   미실현: +1.5% ($3.50)
   손절: $2.2981 | 익절: $2.4388

📊 7일 통계:
  승률: 65% (13/20)
  평균 수익: +3.2%
  평균 손실: -1.8%
  PF: 2.31
  최대 낙폭: -2.1%
  
🔧 시스템: 정상 | 가동시간: 72h
━━━━━━━━━━━━━━━━━━━━━━
```

---

## 텔레그램 알림

### 알림 종류

```
1. 매매 알림 (진입/청산 즉시)
   🟢 LONG 진입 | XRP @ $2.3450
   지표: MA✅ RSI✅ BB⬜ MTF✅ (3/4)
   수량: 100 XRP | 레버: 3x
   SL: $2.2981 (-2%) | TP: $2.4388 (+4%)

   🔴 청산 | TP_HIT +4.00%
   순수익: +$8.91 (수수료 $0.47 차감)
   보유: 4.0시간

2. 일일 서머리 (매일 00:00)
   위의 일일 리포트 형식

3. 경고 알림 (즉시)
   ⚠️ API 에러 3회 연속
   ⚠️ 일일 손실 -3% 도달 (자동 매매 중지)
   ⚠️ 잔고 부족
   🛑 봇 비정상 종료

4. 주간 서머리 (일요일 00:00)
   주간 수익률, 승률, 최적/최악 매매, 지표별 성과
```

---

## 리스크 관리 (하드 리밋)

```
1. 일일 최대 손실: -3% → 당일 매매 자동 중지, 텔레그램 경고
2. 일일 최대 매매 횟수: 5회 → 초과 시 매매 중지
3. 연속 손절 3회 → 2시간 쿨다운
4. 단일 포지션 최대: 자본의 10%
5. 레버리지 최대: 5x (초기에는 1x로 시작)
6. 항상 1개 포지션만 (중복 진입 금지)
```

---

## .env 파일 구조

```env
# Bybit API
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
BYBIT_TESTNET=true

# 텔레그램
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 전략 파라미터
SYMBOL=XRPUSDT
LEVERAGE=1
POSITION_SIZE_PCT=5
STOP_LOSS_PCT=2.0
TAKE_PROFIT_PCT=4.0
TRAILING_STOP_ACTIVATE_PCT=2.0
TRAILING_STOP_CALLBACK_PCT=1.0

# 리스크 관리
MAX_DAILY_LOSS_PCT=3.0
MAX_DAILY_TRADES=5
COOLDOWN_AFTER_SL_STREAK=3
COOLDOWN_HOURS=2

# 로그
LOG_DIR=./logs
LOG_LEVEL=DEBUG
```

---

## 파일 구조

```
xrp-trading-bot/
├── CLAUDE.md              # 이 파일
├── .env                   # 시크릿 (git 제외)
├── .env.example           # 템플릿
├── requirements.txt
├── bot.py                 # 메인 엔트리포인트
├── src/
│   ├── __init__.py
│   ├── config.py          # .env 로드 + 설정 관리
│   ├── exchange.py        # Bybit API 래퍼
│   ├── indicators.py      # 기술적 지표 계산
│   ├── strategy.py        # MA+RSI+BB+MTF 전략 로직
│   ├── risk_manager.py    # 리스크 관리 (하드리밋, 쿨다운)
│   ├── position.py        # 포지션 관리 (진입/청산/트레일링)
│   ├── logger.py          # 로그 시스템 (파일 + 콘솔)
│   ├── telegram_bot.py    # 텔레그램 알림
│   └── utils.py           # 유틸리티
├── logs/                  # 로그 디렉토리
│   ├── trades/
│   ├── signals/
│   ├── equity/
│   └── errors/
└── tests/
    ├── test_indicators.py
    ├── test_strategy.py
    └── test_risk_manager.py
```

---

## 실행 방법

```bash
# 테스트넷
python3 bot.py --testnet

# 실전 (주의!)
python3 bot.py

# 백그라운드 실행
nohup python3 bot.py > /dev/null 2>&1 &

# 로그 실시간 확인
tail -f logs/bot.log

# launchd로 자동 재시작 (Mac)
# ~/Library/LaunchAgents/com.xrp-bot.plist 생성
```

---

## 개발 우선순위

1. config.py + exchange.py (Bybit 연결)
2. indicators.py (지표 계산 — 백테스트 코드 재활용)
3. strategy.py (시그널 생성 + 조합)
4. position.py + risk_manager.py (매매 실행 + 리스크)
5. logger.py (로그 시스템)
6. telegram_bot.py (알림)
7. bot.py (메인 루프 조립)
8. tests/ (단위 테스트)
9. 테스트넷 실행 + 검증
