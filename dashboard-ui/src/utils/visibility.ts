import type { WorkerState } from '../types/domain';

/** Dead workers older than this drop into the "N hidden" bucket. */
export const HIDDEN_CUTOFF_MS = 5 * 60_000;

/**
 * A worker is hidden when:
 *   - it's in the gone bucket (`dead` or `unknown`)
 *   - AND it has been there for at least HIDDEN_CUTOFF_MS
 *
 * `deadSince` is stamped by useNotifications when a worker first
 * transitions into dead. Workers that have never been stamped (e.g.,
 * the very first snapshot before any diff) are always visible.
 */
export function isHidden(
  state: WorkerState,
  workerId: string,
  deadSince: Map<string, number>,
  now: number
): boolean {
  if (state !== 'dead' && state !== 'unknown') return false;
  const since = deadSince.get(workerId);
  if (!since) return false;
  return now - since >= HIDDEN_CUTOFF_MS;
}
