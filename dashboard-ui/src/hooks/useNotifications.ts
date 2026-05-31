/**
 * Subscribes to host transitions and dispatches notifications:
 *   - In-dashboard: marks pulses on the affected workers (instant).
 *   - OS push: debounced 3s window, gated by visibilityState + hasFocus.
 *
 * DESIGN.md Section 5.1 / 5.2 / 7.2.
 *
 * Mount once at the App root. Notification permission UX lives in
 * <NotificationToggle/> — this hook just respects the current permission.
 */

import { useEffect, useRef } from 'react';
import type { Host } from '../types/domain';
import { useDashboardStore } from '../store/dashboard';
import {
  createTransitionDebouncer,
  type PushItem,
} from '../utils/debounceTransitions';
import { diffWorkers } from '../utils/diffWorkers';

const DEBOUNCE_WINDOW_MS = 3000;
const PULSE_BORDER_MS = 1500;
const MAILBOX_BUMP_MS = 300;
const NEW_BADGE_MS = 10_000;

function isDashboardVisible(): boolean {
  if (typeof document === 'undefined') return false;
  return document.visibilityState === 'visible' && document.hasFocus();
}

function sendOSPush(batch: PushItem): void {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission !== 'granted') return;
  if (isDashboardVisible()) return; // dashboard has focus → no OS push

  const items = [...batch.awaiting, ...batch.rate_limited];
  if (items.length === 0) return;

  if (items.length === 1) {
    const t = items[0]!;
    const dot = t.kind === 'awaiting' ? '🟡' : '🔴';
    // We only enter the single-item branch for awaiting/rate_limited, both
    // of which carry a `worker` field — no need to widen the runtime check.
    const w = (t as { worker: { name: string; detail?: string } }).worker;
    const host = (t as { host: string }).host;
    const team = (t as { team: string }).team;
    new Notification(`${dot} ${w.name}`, {
      body: `${w.detail ?? 'state changed'}\n${host} · ${team}`,
    });
    return;
  }

  // Multi-item batch (Section 5.2 debounce format)
  const names = items.map(
    (t) => (t as { worker: { name: string } }).worker.name
  );
  const hosts = new Set(items.map((t) => (t as { host: string }).host));
  const where = hosts.size === 1 ? [...hosts][0]! : 'Multiple hosts';
  new Notification(`${items.length} workers awaiting input`, {
    body: `${names.join(', ')}\n${where}`,
  });
}

export function useNotifications(): void {
  const lastHostsRef = useRef<Host[]>([]);
  const markPulse = useDashboardStore((s) => s.markPulse);
  const markNew = useDashboardStore((s) => s.markNew);
  const markDead = useDashboardStore((s) => s.markDead);
  const clearDead = useDashboardStore((s) => s.clearDead);

  useEffect(() => {
    const debouncer = createTransitionDebouncer(
      DEBOUNCE_WINDOW_MS,
      (batch) => {
        sendOSPush(batch);
        // 'new' badge attaches to awaiting workers only — rate_limited
        // doesn't ask the user for input.
        for (const t of batch.awaiting) {
          markNew((t as { worker: { id: string } }).worker.id, NEW_BADGE_MS);
        }
      }
    );

    const unsub = useDashboardStore.subscribe((state) => {
      // Same reference → not a host update (probably a pulse/new map change).
      if (state.hosts === lastHostsRef.current) return;
      const prev = lastHostsRef.current;
      const transitions = diffWorkers(prev, state.hosts);
      lastHostsRef.current = state.hosts;
      if (transitions.length === 0) return;

      for (const t of transitions) {
        switch (t.kind) {
          case 'awaiting':
            markPulse(t.worker.id, 'amber', PULSE_BORDER_MS);
            break;
          case 'rate_limited':
            markPulse(t.worker.id, 'red', PULSE_BORDER_MS);
            break;
          // v2: dispatch is signalled by the row's →/← arrow opacity
          // transition driven by worker.dispatchOut/In directly, so no
          // pulse is fired here. The flag flip from polling handles it.
          case 'mailbox_new':
            markPulse(t.worker.id, 'scale', MAILBOX_BUMP_MS);
            break;
          case 'dead':
            // Stamp when the worker entered dead so the 5-minute auto-hide
            // bucket can include it (HostGroup reads deadSince).
            markDead(t.worker.id);
            break;
          case 'worker_added':
            // First-discovery snapshot: if the worker arrives already
            // dead (host stale before we ever saw it alive), start the
            // 5-min hidden timer now — otherwise no `dead` transition
            // ever fires and the row stays visible forever.
            if (t.worker.state === 'dead' || t.worker.state === 'unknown') {
              markDead(t.worker.id);
            }
            break;
          // worker_removed / idle have no pulse — the card unmount /
          // state badge already conveys them visually.
        }
      }

      // Resurrection: clear any dead stamp on workers that left the dead/
      // unknown bucket (diffWorkers doesn't emit a `resurrection` kind).
      const prevById = new Map<string, string>();
      for (const h of prev) {
        for (const tm of h.teams) {
          for (const w of tm.workers) prevById.set(w.id, w.state);
        }
      }
      for (const h of state.hosts) {
        for (const tm of h.teams) {
          for (const w of tm.workers) {
            const before = prevById.get(w.id);
            const wasGone = before === 'dead' || before === 'unknown';
            const nowGone = w.state === 'dead' || w.state === 'unknown';
            if (wasGone && !nowGone) clearDead(w.id);
          }
        }
      }

      const pushable = transitions.filter(
        (t) =>
          (t.kind === 'awaiting' || t.kind === 'rate_limited') &&
          // No OS push for the pane the user is currently viewing — when you're
          // typing in a worker/orchestrator pane it goes `typed` (→ awaiting),
          // but you're already looking at it, so an alert is pure noise. The
          // in-dashboard pulse above still fires for visual feedback.
          !('worker' in t && t.worker.focused)
      );
      if (pushable.length > 0) {
        debouncer.push(pushable);
      }
    });

    return () => {
      debouncer.cancel();
      unsub();
    };
  }, [markPulse, markNew]);
}
