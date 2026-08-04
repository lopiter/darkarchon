import { create } from 'zustand';
import type { Host, InactiveTeam, Worker } from '../types/domain';
import type { RawStatusResponse } from '../types/raw';
import { sortWorkersForTeam } from '../utils/sortWorkers';
import { inactiveTeams, transformRawStatus } from '../utils/transform';
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

/** Which top-level view is showing: the card dashboard or the canvas graph. */
export type ViewMode = 'cards' | 'graph';

const VIEW_STORAGE_KEY = 'darkarchon-view';

function readInitialView(): ViewMode {
  try {
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    if (saved === 'cards' || saved === 'graph') return saved;
  } catch {
    /* localStorage unavailable (private mode / SSR) — use default */
  }
  return 'graph';
}
export interface PulseEntry {
  color: PulseColor;
  /** Monotonic counter — bumped each markPulse() so the same color → same color
   * still produces a new key and the CSS animation restarts. */
  key: number;
}

interface DashboardStore {
  hosts: Host[];
  /** Teams the hub knows about with nothing running — cleanup candidates.
   *  Not part of `hosts` because that tree is built from reporting workers. */
  inactiveTeams: InactiveTeam[];
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

  /** Graph view round — current top-level view, persisted to localStorage. */
  view: ViewMode;
  setView: (view: ViewMode) => void;

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

  /**
   * Mark a worker's finished-but-unreviewed result as seen: optimistic local
   * clear + POST /api/ack. Called from selectWorker (opening the panel IS
   * looking at the result) — no-op for workers with nothing unseen.
   */
  ackWorker: (id: string) => void;
  /** Clear every unseen-done marker (header "clear" button). */
  ackAll: () => void;
}

/** Set unseenDone=false on the matching worker (or all when id is null). */
function clearUnseen(hosts: Host[], id: string | null): Host[] {
  return hosts.map((h) => ({
    ...h,
    teams: h.teams.map((t) => ({
      ...t,
      workers: t.workers.map((w) =>
        w.unseenDone && (id === null || w.id === id)
          ? { ...w, unseenDone: false }
          : w
      ),
    })),
  }));
}

/** Fire-and-forget ack POST. Polling re-syncs state, so failures are safe
 * to swallow — the marker simply reappears on the next poll. */
function postAck(body: Record<string, unknown>): void {
  fetch('/api/ack', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {});
}

let pulseSeq = 0;

export const useDashboardStore = create<DashboardStore>((set, get) => ({
  hosts: [],
  inactiveTeams: [],
  hiddenExpanded: {},
  pulseUntil: new Map(),
  newUntil: new Map(),
  deadSince: new Map(),
  selectedWorkerId: null,
  view: readInitialView(),

  setView: (view) => {
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, view);
    } catch {
      /* ignore persistence failures */
    }
    set({ view });
  },

  setRawStatus: (raw) =>
    set({ hosts: transformRawStatus(raw), inactiveTeams: inactiveTeams(raw) }),
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

  selectWorker: (id) => {
    set({ selectedWorkerId: id });
    get().ackWorker(id);
  },
  closePanel: () => set({ selectedWorkerId: null }),

  ackWorker: (id) => {
    const { hosts } = get();
    for (const h of hosts) {
      for (const t of h.teams) {
        const w = t.workers.find((w) => w.id === id);
        if (!w) continue;
        if (!w.unseenDone) return;
        postAck({ host: h.id, target: w.tmuxTarget });
        set({ hosts: clearUnseen(hosts, id) });
        return;
      }
    }
  },

  ackAll: () => {
    postAck({ all: true });
    set((s) => ({ hosts: clearUnseen(s.hosts, null) }));
  },
}));
