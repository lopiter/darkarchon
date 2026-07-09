# darkarchon — Dashboard UI

Phase 1 (Core) prototype. React + TypeScript + Vite + Zustand + CSS Modules.

Backend (`dashboard.py`) integration is Phase 3. Phase 1 validates the design.md spec visually using dummy data only.

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
├── types/domain.ts                 UI tree (Host/Team/Worker)
├── utils/transform.ts              raw → domain + isHostStale
├── utils/sortWorkers.ts            DESIGN.md Section 4.3 sort rules
├── store/dashboard.ts              Zustand
└── mocks/                          dummy + question overlay (Phase 1 only)
```

## Reference docs

- `../DESIGN.md` — single source of truth for design (consult the design director before making changes)
- `../implementation_plan_phase1.md` — build plan and verification checklist for this Phase
