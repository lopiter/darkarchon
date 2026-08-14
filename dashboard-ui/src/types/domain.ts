/**
 * UI tree — what components receive.
 *
 * Constructed by `transformRawStatus` from `RawStatusResponse`.
 * `awaiting_user:question` doesn't exist in raw; it's overlaid via SSE
 * events in Phase 2 (or `applyQuestionOverlay` in Phase 1 mocks).
 */

import type { RawDispatchEntry, RawTask, RawTeamTier } from './raw';

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
  /**
   * Invited pane the team does not own. Shutdown treats these apart: the
   * session belongs to whoever invited it, so killing it would take down
   * windows that have nothing to do with this team.
   */
  external: boolean;
  detail?: string;
  isOrchestrator: boolean;
  /** Worker name of whoever spawned this one — lineage, e.g. 'hermes'. */
  spawnedBy?: string;
  /**
   * Claude Code cross-session messaging address (e.g. 'darkarchon-c3').
   * Copy it into any local Claude session to SendMessage this worker directly.
   * Undefined when the pane hosts no live messaging-capable Claude session.
   */
  peerName?: string;
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
  /**
   * The worker finished a turn (busy→idle) that the user hasn't looked at
   * yet — not a state (the state machine correctly says idle/busy), but an
   * unread marker derived from hub `finished_at > acked_at`. Cleared by
   * viewing the tmux pane, opening the detail panel, or ack-all.
   */
  unseenDone: boolean;
  /** Epoch ms of the last busy→idle transition (undefined if none recorded). */
  finishedAtMs?: number;
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
  /** Aging metadata from the hub's team index. Absent when the hub predates
   *  it, or for synthetic buckets like '(unknown)' that match no state dir. */
  activity?: TeamActivity;
  /** Absolute path of the team's state dir on its host. Same source and same
   *  absence cases as `activity` — needed because prune/archive address a team
   *  by directory, not by name (a worktree team's name is not its path). */
  stateDir?: string;
}

/** Aging summary for one team, from `/api/status`'s `teams` array. */
export interface TeamActivity {
  tier: RawTeamTier;
  idleSeconds: number | null;
  /** 'dispatch' | 'heartbeat' | 'registry' | 'mailbox' — which signal was
   *  newest. Distinguishes "went quiet after working" from "never worked". */
  source: string | null;
  registeredWorkers: number;
  sizeBytes: number;
}

/** A team with no workers currently reporting — cleanup candidate. */
export interface InactiveTeam extends TeamActivity {
  name: string;
  /** Owning host — shown in the list because two hosts can have same-named
   *  teams that have nothing to do with each other. */
  host: string;
  stateDir: string;
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
