/**
 * Polls hub `/api/status` and feeds the store. Phase 3 baseline — SSE is
 * a later round. Polling cadence adapts: 5s by default, 1.5s while the
 * detail panel is open (DESIGN.md Section 6.2).
 *
 * Vite dev proxies `/api/*` to `http://localhost:8774` (see vite.config.ts).
 * In production, the dashboard is meant to be served by the hub itself,
 * so the same relative path works.
 */

import { useEffect } from 'react';
import { useDashboardStore } from '../store/dashboard';
import type { RawStatusResponse } from '../types/raw';

const POLL_IDLE_MS = 5_000;
const POLL_OPEN_MS = 1_500;

export function useHubPolling(): void {
  const panelOpen = useDashboardStore((s) => s.selectedWorkerId !== null);

  useEffect(() => {
    if (import.meta.env.VITE_USE_DUMMY === '1') return;

    const interval = panelOpen ? POLL_OPEN_MS : POLL_IDLE_MS;
    let cancelled = false;

    const tick = async () => {
      try {
        const r = await fetch('/api/status', { cache: 'no-store' });
        if (!r.ok) throw new Error(`hub status ${r.status}`);
        const raw = (await r.json()) as RawStatusResponse;
        if (!cancelled) {
          useDashboardStore.getState().setRawStatus(raw);
        }
      } catch (e) {
        // Stay quiet — the UI keeps its last good snapshot. Logging only
        // in dev so a flapping hub doesn't spam production consoles.
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn('[hub] poll failed', e);
        }
      }
    };

    tick(); // immediate first call
    const id = setInterval(tick, interval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [panelOpen]);
}
