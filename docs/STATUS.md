# tradingbot Status

> Updated: 2026-02-26
> Canonical root: `/Users/jidong/xrp-trading-bot`
> Master plan: `docs/PRODUCTION_MASTER_PLAN_2026-02-26.md`

## Phase Progress

| Phase | Status | Gate |
|------:|:------:|------|
| Phase 0: 코드 위생 + 테스트 고정 | ✅ | `python3 -m pytest -q` (130 passed) + `python3 -m compileall -q src bot.py` |
| Phase A: 테스트넷/드라이런 2주 안정화 | ⏳ | `python bot.py --testnet` (실전 주문 금지) |
| Phase B: 실전 소액(1x, 1~2%) | ⏸ | Phase A 통과 + 체크리스트 |
| Phase C: 운영 자동화 | ⏸ | watchdog + 자동중지 검증 |

## Strategy

| 전략 | Feature Flag | 상태 |
|------|-------------|------|
| Plan A: MA+RSI+BB+MTF (1H) | `SCALP_MODE=false` (기본) | 운영 가능 |
| Plan B: 15m필터+5m스캘핑 (v2) | `SCALP_MODE=true` | v2 개선 완료, 테스트넷 검증 필요 |

## North Star / Guardrails

- North Star: 30D net PnL% (after fees)
- Guardrails: 30D MDD, 일일 최대 손실 트리거, 주문 실패율/API 에러율, 슬리피지 초과 비율

## Latest Change

- **2026-02-26 (v2)**: Plan B 스캘핑 전략 개선
  - **레짐 필터**: ADX + BB width 기반 횡보장 회피 (`SCALP_REGIME_FILTER`)
  - **스프레드 필터**: orderbook 스프레드 체크 → 스캘핑 진입 경로에 통합
  - **수수료+슬리피지 버퍼**: SL/TP에 `SCALP_FEE_BUFFER_PCT` 반영 (서버사이드 SL/TP 보정)
  - **시간 청산 2단계**: 브레이크이븐 30분 (`TIME_EXIT_BE`) + 하드 45분 (`TIME_EXIT`)
  - **MFE/MAE/R-multiple**: 매 거래별 진단 메트릭 (running high/low → trade_data)
  - 24개 신규 테스트 추가 (전체 130개 통과)
- **2026-02-26 (v1)**: Plan B 스캘핑 전략 구현 (`src/strategy_scalp.py`)
  - 15m EMA50/200 추세 필터
  - 5m 풀백 트리거 (EMA20 근접 + RSI + 캔들 방향)
  - 5m BB 브레이크아웃 트리거 (밴드 돌파 + 볼륨)
  - 스캘핑 전용 SL/TP/트레일링/시간청산 파라미터
  - `SCALP_MODE` feature flag로 전략 전환 (기존 전략 보존)

## Plan B v2 Gates (테스트넷 검증 항목)

- [ ] 레짐 필터: 횡보장에서 시그널이 차단되는지 확인 (로그: `REGIME_FILTER`)
- [ ] 스프레드 필터: 넓은 스프레드에서 진입이 차단되는지 확인
- [ ] 수수료 버퍼: 서버사이드 SL/TP 가격이 보정된 값인지 확인
- [ ] 시간 청산: 30분 브레이크이븐 + 45분 하드 청산 동작 확인
- [ ] MFE/MAE: trade log에 mfe_pct, mae_pct, r_multiple이 기록되는지 확인
- [ ] 기존 Plan A 전략이 SCALP_MODE=false에서 영향 없이 동작

## Verification Commands

```bash
cd /Users/jidong/xrp-trading-bot
python3 -m pytest -q
python3 -m compileall -q src bot.py
```

## Next Up (P0)

- Plan B v2 테스트넷 검증 (`SCALP_MODE=true python3 bot.py --testnet`)
- 레짐 필터 파라미터 튜닝 (ADX/BB width 임계값)
- 수수료 버퍼 실측 검증 (실제 슬리피지 vs 추정치)
- Phase A Gate를 CI처럼 매번 돌리기(테스트 깨지면 merge 금지)
- 실전/테스트넷/드라이런 모드 실수 방지(기본은 무조건 안전)

## Worklog

- dev_blog 자동 로그: `/Users/jidong/dev_blog/logs/YYYY-MM-DD/tradingbot-<sha>.md`
