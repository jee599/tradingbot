# tradingbot Status

> Updated: 2026-02-26
> Canonical root: `/Users/jidong/xrp-trading-bot`
> Master plan: `docs/PRODUCTION_MASTER_PLAN_2026-02-26.md`

## Phase Progress

| Phase | Status | Gate |
|------:|:------:|------|
| Phase 0: 코드 위생 + 테스트 고정 | ✅ | `python3 -m pytest -q` (106 passed) + `python3 -m compileall -q src bot.py` |
| Phase A: 테스트넷/드라이런 2주 안정화 | ⏳ | `python bot.py --testnet` (실전 주문 금지) |
| Phase B: 실전 소액(1x, 1~2%) | ⏸ | Phase A 통과 + 체크리스트 |
| Phase C: 운영 자동화 | ⏸ | watchdog + 자동중지 검증 |

## Strategy

| 전략 | Feature Flag | 상태 |
|------|-------------|------|
| Plan A: MA+RSI+BB+MTF (1H) | `SCALP_MODE=false` (기본) | 운영 가능 |
| Plan B: 15m필터+5m스캘핑 | `SCALP_MODE=true` | 구현 완료, 테스트넷 검증 필요 |

## North Star / Guardrails

- North Star: 30D net PnL% (after fees)
- Guardrails: 30D MDD, 일일 최대 손실 트리거, 주문 실패율/API 에러율, 슬리피지 초과 비율

## Latest Change

- **2026-02-26**: Plan B 스캘핑 전략 구현 (`src/strategy_scalp.py`)
  - 15m EMA50/200 추세 필터
  - 5m 풀백 트리거 (EMA20 근접 + RSI + 캔들 방향)
  - 5m BB 브레이크아웃 트리거 (밴드 돌파 + 볼륨)
  - 스캘핑 전용 SL/TP/트레일링/시간청산 파라미터
  - `SCALP_MODE` feature flag로 전략 전환 (기존 전략 보존)
  - 25개 단위 테스트 추가 (전체 106개 통과)

## Verification Commands

```bash
cd /Users/jidong/xrp-trading-bot
python3 -m pytest -q
python3 -m compileall -q src bot.py
```

## Next Up (P0)

- Plan B 테스트넷 검증 (`SCALP_MODE=true python3 bot.py --testnet`)
- Phase A Gate를 CI처럼 매번 돌리기(테스트 깨지면 merge 금지)
- 실전/테스트넷/드라이런 모드 실수 방지(기본은 무조건 안전)
- Runbook대로 재시작/로그/알림 흐름 점검

## Worklog

- dev_blog 자동 로그: `/Users/jidong/dev_blog/logs/YYYY-MM-DD/tradingbot-<sha>.md`
