/**
 * Subscribes to hub Server-Sent Events (`/api/events`) for low-latency
 * notifications that don't wait for the next 5s polling tick.
 *
 * Handled today:
 *   - `new_question`: pulse + "new" badge (Section 5.1 core moment)
 *   - `state_change`: pulse / markDead matching the worker's new raw
 *     state. Polling still backs everything; SSE just makes the pulse
 *     fire immediately instead of up to 5s later.
 *
 * VITE_USE_DUMMY=1 short-circuits (dummy mode shouldn't touch the hub).
 */

import { useEffect } from 'react';
import { useDashboardStore } from '../store/dashboard';

const PULSE_AMBER_MS = 1500;
const PULSE_RED_MS = 1500;
const NEW_BADGE_MS = 10_000;

interface HubEvent {
  type: string;
  [k: string]: unknown;
}

interface NewQuestionEvent extends HubEvent {
  type: 'new_question';
  question_id?: string;
  from_worker?: string;
  body?: string;
}

interface StateChangeEvent extends HubEvent {
  type: 'state_change';
  host?: string;
  worker?: { target?: string };
  from?: string;
  to?: string;
}

function findWorkerIdByName(name: string): string | null {
  const { hosts } = useDashboardStore.getState();
  for (const h of hosts) {
    for (const t of h.teams) {
      const w = t.workers.find((w) => w.name === name);
      if (w) return w.id;
    }
  }
  return null;
}

export function useEventStream(): void {
  useEffect(() => {
    if (import.meta.env.VITE_USE_DUMMY === '1') return;

    const src = new EventSource('/api/events');

    src.onmessage = (e) => {
      let ev: HubEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      const { markPulse, markNew, markDead } = useDashboardStore.getState();

      if (ev.type === 'new_question') {
        const q = ev as NewQuestionEvent;
        const fromWorker = q.from_worker;
        if (!fromWorker) return;
        const id = findWorkerIdByName(fromWorker);
        if (!id) return;
        markPulse(id, 'amber', PULSE_AMBER_MS);
        markNew(id, NEW_BADGE_MS);
        return;
      }

      if (ev.type === 'state_change') {
        const sc = ev as StateChangeEvent;
        if (!sc.host || !sc.worker?.target) return;
        const id = `${sc.host}:${sc.worker.target}`;
        switch (sc.to) {
          case 'typed':
            markPulse(id, 'amber', PULSE_AMBER_MS);
            break;
          case 'rate_limited':
            markPulse(id, 'red', PULSE_RED_MS);
            break;
          case 'dead':
            markDead(id);
            break;
          // idle / busy / compacting / unknown — no instant pulse;
          // polling diff will reconcile the row state on the next tick.
        }
      }
    };

    src.onerror = () => {
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.warn('[sse] connection error — EventSource will auto-retry');
      }
    };

    return () => src.close();
  }, []);
}
