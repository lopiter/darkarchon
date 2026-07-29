/**
 * UI tree — what components receive.
 *
 * Constructed by `transformRawStatus` from `RawStatusResponse`.
 * `awaiting_user:question` doesn't exist in raw; it's overlaid via SSE
 * events in Phase 2 (or `applyQuestionOverlay` in Phase 1 mocks).
 */

import type { RawDispatchEntry, RawTask } from './raw';

export type WorkerState =
  | 'idle'
  | 'busy'
  | 'compacting'
  | 'awaiting_user:typed'
  | 'awaiting_user:question'
  | 'awaiting_user:permission'
  | 'rate_limited'
  | 'dead'
  | 'unknown';

export interface Worker {
  /** `${host}:${tmuxTarget}` — stable id */
  id: string;
  name: string;
  state: WorkerState;
  role: string;
  tmuxTarget: string;
  /** Agent process type — 'claude' | 'codex' | other */
  process: string;
  detail?: string;
  isOrchestrator: boolean;
  /**
   * True when the user is currently viewing this pane (attached tmux client,
   * active window + pane). OS push is suppressed for the focused pane — no
   * alert needed for the pane you're already looking at / typing in.
   * Optional: undefined (legacy agents) → treated as not-focused → notify.
   */
  focused?: boolean;
  /**
   * Sort key for awaiting cards (DESIGN.md Section 4.3).
   * Phase 1: dummy/overlay sets it explicitly.
   * Phase 3: source TBD (frontend tracks state change OR backend adds field).
   */
  enteredStateAt: string;
  /** Tier 2 indicators (DESIGN.md Section 4.2). */
  dispatchOut: boolean;
  dispatchIn: boolean;
  mailboxPending: number;

  /** Phase 3 panel-only fields — raw passthrough so the panel can render
   * without re-fetching per worker. */
  incomingDispatches: RawDispatchEntry[];
  outgoingDispatches: RawDispatchEntry[];
  mailboxSenders: string[];
  recentTasks: RawTask[];
  /** Phase 3.5 placeholder — hub backend currently doesn't expose tail. */
  recentOutput?: string[];
}

export interface Team {
  /** Uppercase label, e.g. 'MYTEAM' */
  name: string;
  workers: Worker[];
}

export interface Host {
  id: string;
  teams: Team[];
  /**
   * (response.ts - host_last_seen) * 1000.
   * > HOST_STALE_MS (15_000) → host is stale (dim cards, show age).
   */
  lastPingMs: number;
  /** Cards auto-hidden after 5 min in dead. Phase 3 introduces the timer. */
  hiddenCount: number;
}
