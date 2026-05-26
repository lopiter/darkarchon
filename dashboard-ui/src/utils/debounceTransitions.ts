/**
 * 3-second window debouncer for OS-push-worthy transitions.
 *
 * DESIGN.md Section 5.2:
 *   > 3초 window 내에 awaiting이 N개 발생하면 1개 알림으로 합침
 *
 * Only `awaiting` and `rate_limited` transitions feed OS push notifications.
 * Mailbox/dispatch transitions stay in-dashboard (pulse only) and are
 * filtered out at the push boundary, never reaching the debouncer.
 *
 * Behavior:
 *   - First push starts the window (windowMs).
 *   - Subsequent pushes within the window append to the buffer.
 *   - On window expiry, `onFlush` is called once with the merged batch
 *     and internal state resets.
 *   - `cancel()` drops the buffer and clears the timer (for unmount).
 *
 * Stateless wrt React — accepts any timer source (used with fake timers
 * in tests). No singleton; instantiate one per consumer.
 */

import type { Transition } from './diffWorkers';

export interface PushItem {
  awaiting: Transition[];
  rate_limited: Transition[];
}

export interface TransitionDebouncer {
  push: (transitions: Transition[]) => void;
  cancel: () => void;
}

function isPushable(t: Transition): boolean {
  return t.kind === 'awaiting' || t.kind === 'rate_limited';
}

export function createTransitionDebouncer(
  windowMs: number,
  onFlush: (batch: PushItem) => void
): TransitionDebouncer {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const buffer: PushItem = { awaiting: [], rate_limited: [] };

  const reset = () => {
    buffer.awaiting = [];
    buffer.rate_limited = [];
    timer = null;
  };

  const flush = () => {
    const batch: PushItem = {
      awaiting: [...buffer.awaiting],
      rate_limited: [...buffer.rate_limited],
    };
    reset();
    if (batch.awaiting.length > 0 || batch.rate_limited.length > 0) {
      onFlush(batch);
    }
  };

  return {
    push(transitions) {
      let appended = false;
      for (const t of transitions) {
        if (!isPushable(t)) continue;
        if (t.kind === 'awaiting') buffer.awaiting.push(t);
        else if (t.kind === 'rate_limited') buffer.rate_limited.push(t);
        appended = true;
      }
      if (!appended) return;
      if (timer === null) {
        timer = setTimeout(flush, windowMs);
      }
    },
    cancel() {
      if (timer !== null) {
        clearTimeout(timer);
      }
      reset();
    },
  };
}
