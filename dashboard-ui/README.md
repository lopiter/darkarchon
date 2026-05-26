# darkarchon — Dashboard UI

Phase 1 (Core) prototype. React + TypeScript + Vite + Zustand + CSS Modules.

Backend (`dashboard.py`) 연결은 Phase 3. Phase 1 은 더미 데이터로만 design.md spec 시각 검증.

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
npm run preview  # 빌드 결과 로컬 확인
```

## 구조

```
src/
├── App.tsx + main.tsx             entry
├── styles/                         tokens (DESIGN.md Section 2), reset, global
├── components/                     CSS Modules + colocated tsx
├── types/raw.ts                    hub /api/status 응답 (Phase 3 통합 baseline)
├── types/domain.ts                 UI 트리 (Host/Team/Worker)
├── utils/transform.ts              raw → domain + isHostStale
├── utils/sortWorkers.ts            DESIGN.md Section 4.3 정렬 룰
├── store/dashboard.ts              Zustand
└── mocks/                          dummy + question overlay (Phase 1만)
```

## 참고 문서

- `../DESIGN.md` — 디자인 단일 출처 (수정 시 디자인 디렉터 협의)
- `../implementation_plan_phase1.md` — 본 Phase 의 빌드 계획 + 검증 체크리스트
