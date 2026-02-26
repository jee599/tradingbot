# XRP Trading Bot (tradingbot) – 프로덕션 마스터 플랜

- Repo: `jee599/tradingbot`
- Canonical root: `/Users/jidong/xrp-trading-bot`
- Updated: 2026-02-26

목적은 하나다.

**XRP/USDT 선물 자동매매를 “망하지 않게” 굴린다.**

원칙은 이것만 지킨다.

- **실전은 Gate 통과 전엔 안 건드린다.**
- **STATUS는 한 장만 유지한다.**
- 커밋이 생기면 dev_blog에 worklog가 자동으로 남는다(연결만 해두고, 흐름은 유지).

-----

## 0) One-liner

Bybit V5로 멀티코인(BTC/ETH/XRP/SOL) 무기한선물을 자동매매한다.

전략은 2가지를 지원한다 (feature flag 전환):

- **Plan A (Legacy)**: 1H 기준 MA+RSI+BB+MTF 4지표 과반수 투표
- **Plan B (Scalp)**: 15m 추세필터 + 5m 풀백/브레이크아웃 스캘핑

-----

## 1) North Star + Metrics

North Star Metric은 **30일 롤링 순수익률(수수료 포함) + 생존(드로우다운 제한)**이다.

가드레일은 “한 번에 죽는” 케이스를 막는 지표로 둔다.

- North Star
  - 30D net PnL% (after fees)
- Guardrails
  - Max Drawdown (30D) ≤ X% (초기 X=5~8%로 보수적으로)
  - 일일 최대 손실 트리거 발동 횟수(= 멈춘 날 수)
  - 주문 실패율 / API 에러율 (429/timeout 포함)
  - 슬리피지(체결가-의도)가 특정 임계치 초과한 거래 비율

이벤트/로그는 이미 있다. 핵심은 “숫자”로 뽑히게 만드는 거다.

- trades JSON (진입/청산)
- signals JSON (매시간)
- equity CSV (매시간)
- errors log

Gate는 **리포트 1장으로** 확인 가능해야 한다.

-----

## 2) 현재 상태 (As-Is)

### 프로젝트 구조

```text
/Users/jidong/xrp-trading-bot/
├─ bot.py                 # 엔트리 (전략 라우팅: legacy / scalp)
├─ src/
│  ├─ config.py           # .env 로드 + SCALP_MODE feature flag
│  ├─ exchange.py         # Bybit 래퍼
│  ├─ indicators.py       # 지표 (legacy용)
│  ├─ strategy.py         # Plan A: MA+RSI+BB+MTF 시그널/투표
│  ├─ strategy_scalp.py   # Plan B: 15m 필터 + 5m 스캘핑 트리거
│  ├─ risk_manager.py     # 하드 리밋 (공유)
│  ├─ position.py         # 포지션/청산 (공유)
│  ├─ logger.py           # 파일 로그
│  ├─ telegram_bot.py     # 알림
│  └─ utils.py
├─ scripts/
│  ├─ backtest.py
│  ├─ restart_bot.sh
│  └─ update_symbols.py
├─ tests/
│  ├─ test_indicators.py
│  ├─ test_strategy.py
│  ├─ test_strategy_scalp.py   # Plan B 시그널 테스트 (25건)
│  ├─ test_risk_manager.py
│  └─ test_position_mode.py
└─ logs/
```

### 지금까지 된 것(요약)

- **Plan A**: MA+RSI+BB+MTF 과반수 투표 전략
- **Plan B**: 15m EMA 추세필터 + 5m 풀백/BB 브레이크아웃 스캘핑 전략
- Feature flag `SCALP_MODE` (env)로 전략 전환 (기존 전략 보존)
- 리스크 하드 리밋(일일 최대 손실/연속 손절/포지션 제한)
- 로그 체계(매매/시그널/에쿼티/에러)
- 단위 테스트: 106건 전체 통과

### 아직 불안/미완료(런칭 블로커)

- “실전/테스트넷/드라이런” 모드가 헷갈릴 여지(사람 실수 방지)
- 운영 Runbook이 문서로 고정되어 있지 않음(재시작/로그 확인/장애 대응)
- 실전 전환 Gate가 숫자로 정의되지 않음(성급하게 켜기 쉬움)

-----

## 3) Architecture snapshot

핵심은 3레이어다.

- Data: Bybit OHLCV / 계정 / 포지션
- Logic: 지표 → 시그널 → 필터 → 주문
- Ops: 로그/알림/재시작

외부 서비스:
- Bybit V5 API
- Telegram Bot API

-----

## 4) Phases & Gates

### Phase 0: 코드 위생 + 테스트 고정

목표: 손대도 안 깨지는 상태.

Gate:

```bash
cd /Users/jidong/xrp-trading-bot
python3 -m pytest -q
python3 -m compileall -q src bot.py
```

P0 태스크:
- 테스트 커맨드가 로컬에서 항상 돌아가게(의존성/버전 문서화)
- 최소 실행 가이드 1개로 고정(README 대신 docs)

---

### Phase A: 테스트넷(또는 드라이런) 2주 안정화

목표: “죽지 않고”, “기록이 남고”, “멈출 땐 멈추는” 봇.

Gate(재현 가능):

```bash
cd /Users/jidong/xrp-trading-bot
python3 -m pytest -q
# 절대 실전 주문 안 나가게: testnet 또는 dry-run만
python3 bot.py --testnet
```

운영 Gate(숫자):
- 14일 연속 가동(중간 재시작은 OK)
- 주문 실패/예외로 인한 프로세스 다운 0회(= 재시작 원인 분석/조치 완료)
- 일일 손실 제한 트리거가 “의도대로” 동작했는지 로그로 증빙

P0 태스크:
- 안전장치: 실전 모드는 **명시적으로 opt-in** (예: `--live` 같은 강제 플래그)
- 텔레그램 알림 최소 세트: 시작/중지/진입/청산/에러/일일요약

---

### Phase B: 실전 소액(1x, 포지션 1~2%)

목표: 전략 성과보다 “운영 사고 0”을 먼저 본다.

Gate:
- Phase A Gate 통과
- **실전 모드 체크리스트(사람 검증) 통과**
- 30D MDD가 가드레일 내

P0 태스크:
- 실전 전환 체크리스트 문서화(아래 Runbook에 포함)
- 수수료/슬리피지 로그가 net PnL에 반영되는지 확인

---

### Phase C: 운영 자동화(멈춤/복구/요약)

목표: 사람이 계속 쳐다보지 않아도 된다.

Gate:
- 1주일 동안 “이상징후 감지 → 알림 → 자동 중지”가 정확히 동작

P0 태스크:
- watchdog(launchd/supervisor) 표준화
- 장애 템플릿(원인/대응/재발방지) 남기기

-----

## 5) Business / UX / Design (Gate 포함)

### 5.1 Business(= 계좌 생존 + 수익)

이 프로젝트의 사업성은 단순하다.

- **잃지 않기**가 1순위
- 그 다음이 **안정적으로 벌기**

Gate-Business:
- 30D net PnL%가 계산 가능(수수료 포함)
- MDD/일일 손실 제한/연속 손절 쿨다운이 “데이터로” 확인 가능

### 5.2 UX(= 운영자 경험)

사용자는 결국 “나”다.

- 켜는 법이 단순해야 함
- 멈추는 법이 더 단순해야 함
- 로그/알림이 결론부터 말해야 함

Gate-UX:
- 10분 안에: 설치 → 테스트 → 테스트넷 실행 → 로그 확인이 가능
- 에러가 나면: 원인/다음 행동이 텔레그램에 찍힘

### 5.3 Design(= 정보 디자인)

UI 디자인이 아니라 **리포트 디자인**이다.

- 알림은 짧게(한 줄 요약 + 숫자)
- 상세는 로그(JSON/CSV)로 내려보내기
- 파일명/폴더명이 규칙적이어야 함

Gate-Design:
- 일일 요약 1장으로 “오늘 뭐했는지” 끝까지 읽지 않아도 판단 가능

-----

## 6) Runbook (운영)

### 절대 규칙

- `.env`는 건드리지 않는다(특히 실전 키).
- 실전 매매는 Gate 통과 전엔 금지.

### 실행

```bash
cd /Users/jidong/xrp-trading-bot
# 테스트넷
python3 bot.py --testnet

# 로그 확인
tail -f logs/bot.log
```

### 재시작

```bash
cd /Users/jidong/xrp-trading-bot
bash scripts/restart_bot.sh
```

### 장애 대응(최소)

1) 주문 실패/에러 폭증 → 즉시 중지(자동이든 수동이든)
2) `logs/errors/errors.log` 확인
3) 최근 1~2시간 signals/trades/equity 확인
4) 원인 분류
- API/네트워크
- 전략 버그(신호 이상)
- 리스크 설정/포지션 사이징 실수

### 실전 전환 체크리스트(사람 확인)

- [ ] `--testnet`로 14일 로그 정상
- [ ] 일일 손실 제한(-X%)이 동작했고 멈췄다
- [ ] 텔레그램 알림이 빠지지 않는다
- [ ] 포지션 사이즈가 “절대” 10%를 넘지 않는다
- [ ] 실전 키/채팅방이 맞다(잘못된 방에 알림 보내는 사고 방지)

-----

## 7) Status/Worklog linkage

- Single source of truth: `docs/STATUS.md`
- Master plan: `docs/PRODUCTION_MASTER_PLAN_2026-02-26.md`
- dev_blog worklog(자동): `/Users/jidong/dev_blog/logs/YYYY-MM-DD/tradingbot-<sha>.md`

(연결 규칙이 없으면, 최소한 이 경로 컨벤션만 유지한다.)

-----

## 8) 전략 상세

### Plan A (Legacy) — `SCALP_MODE=false`

- 타임프레임: INTERVAL (기본 60분)
- 지표: MA(EMA20/50 + ADX), RSI(14), BB(20,2σ), MTF(4H EMA 근사)
- 시그널: 4지표 과반수 투표 (≥2 동의, 반대 0)
- 청산: SL -2% / TP +4% / 트레일링 +3.5% 활성 -2% 콜백 / 시간 48h
- 최소 confidence: 3/4

### Plan B (Scalp) — `SCALP_MODE=true`

- 추세 필터: 15m EMA50 vs EMA200 → long-only / short-only / no-trade
- 진입 트리거 (5m):
  - **Pullback**: price near EMA20 (0.3%) + 방향 일치 캔들 + RSI 35~65
  - **Breakout**: close > BB upper (long) / < BB lower (short) + volume ≥ 1.5x
- 청산: SL -0.8% / TP +1.4% / 트레일링 +0.8% 활성 -0.4% 콜백 / 시간 45분
- 시그널 주기: 매분
- 심볼: BTCUSDT, ETHUSDT, XRPUSDT, SOLUSDT

-----

## 9) Decisions (선택)

큰 변경은 `docs/DECISIONS.md`에 남긴다.

- 실전 모드 플래그 정책(기본 off)
- 손실 제한/레버리지/포지션 비율 기본값
- 2026-02-26: Plan B 스캘핑 전략 추가 (SCALP_MODE feature flag, 기존 전략 보존)
