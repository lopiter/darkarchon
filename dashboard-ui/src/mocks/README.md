# mocks/

## dummyStatus.ts

`RawStatusResponse` format — 1:1 identical to the hub `/api/status` response. Can be swapped directly for the fetch result during Phase 3 integration. Scenario for screen validation:

| Host | State |
|---|---|
| main | fresh (ping 3s ago) |
| second | slightly stale (ping 7s ago) |
| sim-host | stale (ping 120s ago → >15s threshold) |

**main / MYTEAM** workers are the core of the sort validation:
- `backend` (typed → awaiting_user:typed)
- `frontend` — idle by default, forced to awaiting_user:question when `applyQuestionOverlay` is applied
- `dashboard` (busy, orchestrator)
- `writer` (compacting, external invited)

**main / MYTEAM-VOC_1**:
- `backend` (rate_limited) — a worker of the same name exists concurrently in a different worktree
- `voc` (busy, orchestrator)

## applyQuestionOverlay.ts

`awaiting_user:question` has no state in raw (it is a Phase 2 SSE event). For Phase 1 sort/visual validation, one worker is marked with the question state as a post-processing step on the transform result.

```ts
const hosts = applyQuestionOverlay(
  transformRawStatus(dummyStatus),
  'main',
  'frontend',
  'Which test suite should I run?'
);
```
