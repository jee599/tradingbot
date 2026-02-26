# tradingbot Status

> Updated: 2026-02-26
> Canonical root: `/Users/jidong/xrp-trading-bot`
> Master plan: `docs/PRODUCTION_MASTER_PLAN_2026-02-26.md`

## Phase Progress

| Phase | Status | Gate |
|------:|:------:|------|
| Phase 0: 코드 위생 + 테스트 고정 | ⏳ | `python3 -m pytest -q` + `python3 -m compileall -q src bot.py` |
| Phase A: 테스트넷/드라이런 2주 안정화 | ⏳ | `python bot.py --testnet` (실전 주문 금지) |
| Phase B: 실전 소액(1x, 1~2%) | ⏸ | Phase A 통과 + 체크리스트 |
| Phase C: 운영 자동화 | ⏸ | watchdog + 자동중지 검증 |

## North Star / Guardrails

- North Star: 30D net PnL% (after fees)
- Guardrails: 30D MDD, 일일 최대 손실 트리거, 주문 실패율/API 에러율, 슬리피지 초과 비율

## Latest Change

- Master plan / Status 문서 포맷을 saju 기준으로 정리

## Verification Commands

```bash
cd /Users/jidong/xrp-trading-bot
python3 -m pytest -q
python3 -m compileall -q src bot.py
```

## Next Up (P0)

- Phase 0 Gate를 CI처럼 매번 돌리기(테스트 깨지면 merge 금지)
- 실전/테스트넷/드라이런 모드 실수 방지(기본은 무조건 안전)
- Runbook대로 재시작/로그/알림 흐름 점검

## Worklog

- dev_blog 자동 로그: `/Users/jidong/dev_blog/logs/YYYY-MM-DD/tradingbot-<sha>.md`
