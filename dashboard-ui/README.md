# darkarchon — Dashboard UI

React + TypeScript + Vite + Zustand + CSS Modules.

Hub-connected by default: `useHubPolling` polls `dashboard.py`'s `/api/status` and `useEventStream` subscribes to `/api/events` for instant pulses. Set `VITE_USE_DUMMY=1` to run against the fixed snapshot in `src/mocks/` instead — no hub needed, and the DebugPanel appears for triggering states by hand.

## Dev

```bash
npm install
npm run dev      # http://localhost:5173
```

## Test

```bash
npm test         # vitest single run
npm run test:watch
```

## Build

```bash
npm run build    # → dist/
npm run preview  # inspect build output locally
```

## Structure

```
src/
├── App.tsx + main.tsx             entry
├── styles/                         tokens (DESIGN.md Section 2), reset, global
├── components/                     CSS Modules + colocated tsx
├── types/raw.ts                    hub /api/status response (Phase 3 integration baseline)
├── types/domain.ts                 UI tree (Host/Team/Worker) + team activity
├── components/InactiveTeams/       teams with nothing running, collapsed
├── utils/transform.ts              raw → domain + isHostStale + inactiveTeams
├── utils/sortWorkers.ts            DESIGN.md Section 4.3 sort rules
├── store/dashboard.ts              Zustand
└── mocks/                          dummy snapshot + question overlay (VITE_USE_DUMMY=1)
```

## Reference docs

- `../DESIGN.md` — design spec (consult the design director before making changes)
- `../README.md` — hub, agent, and team lifecycle
