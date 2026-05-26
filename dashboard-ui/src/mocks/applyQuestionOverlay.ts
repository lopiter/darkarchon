import type { Host } from '../types/domain';

/**
 * Phase 1 only — force a specific worker into `awaiting_user:question` state.
 * Raw hub responses don't carry `question` (it arrives via SSE event in Phase 2).
 * This helper lets us visually verify that state in Phase 1 without backend changes.
 */
export function applyQuestionOverlay(
  hosts: Host[],
  hostId: string,
  workerName: string,
  question: string
): Host[] {
  return hosts.map((h) =>
    h.id !== hostId
      ? h
      : {
          ...h,
          teams: h.teams.map((t) => ({
            ...t,
            workers: t.workers.map((w) =>
              w.name !== workerName
                ? w
                : {
                    ...w,
                    state: 'awaiting_user:question',
                    detail: question,
                  }
            ),
          })),
        }
  );
}
