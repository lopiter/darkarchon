/**
 * Auto-focus target selection for the graph view camera.
 *
 * The camera follows the fleet's "attention target" with two priorities:
 *
 *   P1 — a worker awaiting user input (`awaiting_user:*`). The OLDEST
 *        unresolved one wins and HOLDS focus: new awaiting workers queue
 *        behind it instead of stealing the camera, so focus never ping-pongs.
 *        When the held worker leaves awaiting, the next-oldest takes over.
 *   P2 — a worker that just completed (state transitioned to `idle`). Only
 *        considered while NO awaiting worker exists anywhere.
 *
 * Pure state machine: callers thread `AutoFocusState` through and act on the
 * returned `focusId` (null = don't move the camera this tick).
 */

import type { Host } from '../types/domain';
import type { Transition } from './diffWorkers';

export interface AutoFocusState {
  /** Monotonic counter assigning arrival order to awaiting workers. */
  seq: number;
  /** Worker id → arrival order; membership mirrors who is awaiting NOW. */
  awaitingSeq: Map<string, number>;
  /** Node the camera was last auto-sent to (dedupes repeat targets). */
  targetId: string | null;
}

export function initialAutoFocusState(): AutoFocusState {
  return { seq: 0, awaitingSeq: new Map(), targetId: null };
}

function awaitingIds(hosts: Host[]): Set<string> {
  const out = new Set<string>();
  for (const h of hosts) {
    for (const t of h.teams) {
      for (const w of t.workers) {
        if (w.state.startsWith('awaiting_user:')) out.add(w.id);
      }
    }
  }
  return out;
}

export function nextAutoFocus(
  state: AutoFocusState,
  hosts: Host[],
  transitions: Transition[]
): { state: AutoFocusState; focusId: string | null } {
  const seqMap = new Map(state.awaitingSeq);
  let seq = state.seq;

  const nowAwaiting = awaitingIds(hosts);
  for (const id of seqMap.keys()) {
    if (!nowAwaiting.has(id)) seqMap.delete(id);
  }
  for (const id of nowAwaiting) {
    if (!seqMap.has(id)) seqMap.set(id, seq++);
  }

  let targetId = state.targetId;

  if (seqMap.size > 0) {
    // P1: oldest awaiting worker holds the camera.
    let best: string | null = null;
    let bestSeq = Infinity;
    for (const [id, s] of seqMap) {
      if (s < bestSeq) {
        best = id;
        bestSeq = s;
      }
    }
    targetId = best;
  } else {
    // P2: latest completion this tick — but never while anyone is awaiting.
    for (const t of transitions) {
      if (t.kind === 'idle') targetId = t.worker.id;
    }
    // Drop a stale target that left the graph entirely.
    if (targetId !== null) {
      const removed = transitions.some(
        (t) => t.kind === 'worker_removed' && t.workerId === targetId
      );
      if (removed) targetId = null;
    }
  }

  const focusId = targetId !== null && targetId !== state.targetId ? targetId : null;
  return { state: { seq, awaitingSeq: seqMap, targetId }, focusId };
}
