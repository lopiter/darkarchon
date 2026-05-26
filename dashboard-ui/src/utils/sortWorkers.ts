import type { Worker, WorkerState } from '../types/domain';

/**
 * DESIGN.md Section 4.3 — sort within a single team.
 *
 *   1. awaiting_user:typed / awaiting_user:question  (enteredStateAt 역순)
 *   2. rate_limited
 *   3. busy / compacting                              (이름 순)
 *   4. idle                                            (이름 순)
 *   5. dead / unknown                                  (showDead=false 면 제외)
 *
 * Sorting never crosses team boundaries (caller passes one team's workers).
 */
export function sortWorkersForTeam(
  workers: Worker[],
  showDead: boolean
): Worker[] {
  const visible = showDead ? workers : workers.filter(isVisible);
  const groups: Record<1 | 2 | 3 | 4 | 5, Worker[]> = {
    1: [],
    2: [],
    3: [],
    4: [],
    5: [],
  };
  for (const w of visible) groups[priorityOf(w.state)].push(w);

  groups[1].sort((a, b) => b.enteredStateAt.localeCompare(a.enteredStateAt));
  // group 2: leave as-is
  groups[3].sort((a, b) => a.name.localeCompare(b.name));
  groups[4].sort((a, b) => a.name.localeCompare(b.name));
  // group 5: leave as-is

  return [...groups[1], ...groups[2], ...groups[3], ...groups[4], ...groups[5]];
}

function isVisible(w: Worker): boolean {
  return w.state !== 'dead' && w.state !== 'unknown';
}

function priorityOf(s: WorkerState): 1 | 2 | 3 | 4 | 5 {
  if (s === 'awaiting_user:typed' || s === 'awaiting_user:question') return 1;
  if (s === 'rate_limited') return 2;
  if (s === 'busy' || s === 'compacting') return 3;
  if (s === 'idle') return 4;
  return 5;
}
