import type { Worker } from '../types/domain';

/**
 * DESIGN.md Section 4.3 — sort within a single team, triage order:
 *
 *   1. awaiting_user:typed / question / permission   (enteredStateAt desc)
 *   2. rate_limited
 *   3. idle with an unreviewed result (unseenDone)    (finishedAtMs desc)
 *   4. busy / compacting                              (by name)
 *   5. idle                                            (by name)
 *   6. dead / unknown                                  (excluded when showDead=false)
 *
 * The order answers "what should I look at?": blocked-on-me first, then
 * results waiting for review, then things still running.
 * Sorting never crosses team boundaries (caller passes one team's workers).
 */
export function sortWorkersForTeam(
  workers: Worker[],
  showDead: boolean
): Worker[] {
  const visible = showDead ? workers : workers.filter(isVisible);
  const groups: Record<1 | 2 | 3 | 4 | 5 | 6, Worker[]> = {
    1: [],
    2: [],
    3: [],
    4: [],
    5: [],
    6: [],
  };
  for (const w of visible) groups[priorityOf(w)].push(w);

  groups[1].sort((a, b) => b.enteredStateAt.localeCompare(a.enteredStateAt));
  // group 2: leave as-is
  groups[3].sort((a, b) => (b.finishedAtMs ?? 0) - (a.finishedAtMs ?? 0));
  groups[4].sort((a, b) => a.name.localeCompare(b.name));
  groups[5].sort((a, b) => a.name.localeCompare(b.name));
  // group 6: leave as-is

  return [
    ...groups[1],
    ...groups[2],
    ...groups[3],
    ...groups[4],
    ...groups[5],
    ...groups[6],
  ];
}

function isVisible(w: Worker): boolean {
  return w.state !== 'dead' && w.state !== 'unknown';
}

function priorityOf(w: Worker): 1 | 2 | 3 | 4 | 5 | 6 {
  const s = w.state;
  // Every awaiting_user:* variant is "a human has to act" — rank them together
  // so a new one can't silently drop out of the top group.
  if (s.startsWith('awaiting_user:')) return 1;
  if (s === 'rate_limited') return 2;
  if (s === 'idle' && w.unseenDone) return 3;
  if (s === 'busy' || s === 'compacting') return 4;
  if (s === 'idle') return 5;
  return 6;
}
