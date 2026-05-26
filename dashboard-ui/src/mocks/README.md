# mocks/

## dummyStatus.ts

`RawStatusResponse` 형식 — hub `/api/status` 응답과 1:1 동일. Phase 3 통합 시 fetch 결과로 그대로 대체 가능. 화면 검증을 위한 시나리오:

| 호스트 | 상태 |
|---|---|
| main | fresh (3s 전 ping) |
| second | 약간 (7s 전 ping) |
| sim-host | stale (120s 전 ping → >15s 임계) |

**main / MYTEAM** 워커들이 정렬 검증의 핵심:
- `backend` (typed → awaiting_user:typed)
- `frontend` — 기본은 idle, `applyQuestionOverlay` 적용 시 awaiting_user:question 으로 강제
- `dashboard` (busy, orchestrator)
- `writer` (compacting, external invited)

**main / MYTEAM-VOC_1**:
- `backend` (rate_limited) — 동명 워커가 다른 worktree 에 동시 존재
- `voc` (busy, orchestrator)

## applyQuestionOverlay.ts

`awaiting_user:question` 은 raw 에 state 가 없음 (Phase 2 SSE 이벤트). Phase 1 정렬/시각 검증을 위해 transform 결과에 후처리로 한 워커를 question 상태로 마킹.

```ts
const hosts = applyQuestionOverlay(
  transformRawStatus(dummyStatus),
  'main',
  'frontend',
  'Which test suite should I run?'
);
```
