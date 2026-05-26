import { create } from 'zustand';
import type { Host, Worker } from '../types/domain';
import type { RawStatusResponse } from '../types/raw';
import { sortWorkersForTeam } from '../utils/sortWorkers';
import { transformRawStatus } from '../utils/transform';
import { isHidden } from '../utils/visibility';

/**
 * Row-effect keys used by markPulse.
 *   - amber/red: row background + left bar glow pulse (v2 Section 5.1)
 *   - scale:    mailbox badge bump (Section 7.2)
 *
 * v1 had `sweep_out`/`sweep_in` for the dispatch top-bar sweep; v2 drops
 * them because dispatch is now an opacity-toggled arrow next to the
 * status label (no separate effect needed).
 */
export type PulseColor = 'amber' | 'red' | 'scale';
export interface PulseEntry {
  color: PulseColor;
  /** Monotonic counter — bumped each markPulse() so the same color → same color
   * still produces a new key and the CSS animation restarts. */
  key: number;
}

interface DashboardStore {
  hosts: Host[];
  /** hostId → expanded flag for the 'N hidden' dead bucket */
  hiddenExpanded: Record<string, boolean>;

  /** Ephemeral — workerId → active pulse (or absent). Cleared by setTimeout. */
  pulseUntil: Map<string, PulseEntry>;
  /** Ephemeral — workerId → true while the "new" badge is showing. */
  newUntil: Map<string, true>;
  /** workerId → epoch ms when the worker first entered dead state.
   * Cleared on resurrection so a flapping worker doesn't carry a stale
   * timestamp. */
  deadSince: Map<string, number>;

  /** Phase 3 — id of the worker whose detail panel is open. `null` = closed. */
  selectedWorkerId: string | null;

  /** Sole data entry point. Phase 1 dummy and Phase 3 fetch both feed this. */
  setRawStatus: (raw: RawStatusResponse) => void;
  /** Phase 1 helper — applies post-transform mutation (e.g. question overlay) */
  setHosts: (hosts: Host[]) => void;
  toggleHidden: (hostId: string) => void;

  /** Sorted workers for one team. dead/unknown only included when showDead. */
  getSortedTeamWorkers: (
    hostId: string,
    teamName: string,
    showDead: boolean
  ) => Worker[];

  /** Notification side — mark a pulse and auto-clear after durationMs. */
  markPulse: (workerId: string, color: PulseColor, durationMs: number) => void;
  /** Mark a "new" badge and auto-clear after durationMs. */
  markNew: (workerId: string, durationMs: number) => void;
  /** Phase 4+ — stamp a worker as having entered dead state. Idempotent. */
  markDead: (workerId: string, now?: number) => void;
  /** Clear the dead stamp (worker resurrected). */
  clearDead: (workerId: string) => void;

  /** Phase 3 — open the detail panel for a given worker. `null` = closed. */
  selectWorker: (id: string) => void;
  /** Phase 3 — close the detail panel. */
  closePanel: () => void;
}

let pulseSeq = 0;

export const useDashboardStore = create<DashboardStore>((set, get) => ({
  hosts: [],
  hiddenExpanded: {},
  pulseUntil: new Map(),
  newUntil: new Map(),
  deadSince: new Map(),
  selectedWorkerId: null,

  setRawStatus: (raw) => set({ hosts: transformRawStatus(raw) }),
  setHosts: (hosts) => set({ hosts }),

  toggleHidden: (hostId) =>
    set((s) => ({
      hiddenExpanded: {
        ...s.hiddenExpanded,
        [hostId]: !s.hiddenExpanded[hostId],
      },
    })),

  getSortedTeamWorkers: (hostId, teamName, showDead) => {
    const { hosts, deadSince, hiddenExpanded } = get();
    const host = hosts.find((h) => h.id === hostId);
    const team = host?.teams.find((t) => t.name === teamName);
    if (!team) return [];
    const expanded = hiddenExpanded[hostId] ?? false;
    const now = Date.now();
    const filtered = expanded
      ? team.workers
      : team.workers.filter((w) => !isHidden(w.state, w.id, deadSince, now));
    return sortWorkersForTeam(filtered, showDead);
  },

  markPulse: (workerId, color, durationMs) => {
    const key = ++pulseSeq;
    set((s) => {
      const next = new Map(s.pulseUntil);
      next.set(workerId, { color, key });
      return { pulseUntil: next };
    });
    setTimeout(() => {
      set((s) => {
        const cur = s.pulseUntil.get(workerId);
        // Only clear if it's still our entry (newer markPulse would have
        // bumped the key — leave that one running).
        if (!cur || cur.key !== key) return {};
        const next = new Map(s.pulseUntil);
        next.delete(workerId);
        return { pulseUntil: next };
      });
    }, durationMs);
  },

  markNew: (workerId, durationMs) => {
    set((s) => {
      const next = new Map(s.newUntil);
      next.set(workerId, true);
      return { newUntil: next };
    });
    setTimeout(() => {
      set((s) => {
        if (!s.newUntil.has(workerId)) return {};
        const next = new Map(s.newUntil);
        next.delete(workerId);
        return { newUntil: next };
      });
    }, durationMs);
  },

  markDead: (workerId, now = Date.now()) =>
    set((s) => {
      if (s.deadSince.has(workerId)) return {};
      const next = new Map(s.deadSince);
      next.set(workerId, now);
      return { deadSince: next };
    }),
  clearDead: (workerId) =>
    set((s) => {
      if (!s.deadSince.has(workerId)) return {};
      const next = new Map(s.deadSince);
      next.delete(workerId);
      return { deadSince: next };
    }),

  selectWorker: (id) => set({ selectedWorkerId: id }),
  closePanel: () => set({ selectedWorkerId: null }),
}));
