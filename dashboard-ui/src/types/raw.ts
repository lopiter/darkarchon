/**
 * Hub `/api/status` response — 1:1 with backend output.
 *
 * Field names/types must match `dashboard.py:_status()`. Phase 3 integration
 * will pipe `fetch().json()` straight into `setRawStatus`.
 */

export type RawWorkerState =
  | 'idle'
  | 'busy'
  | 'typed' // → domain 'awaiting_user:typed'
  | 'awaiting_permission' // blocked on a tool-permission prompt
  | 'awaiting_user' // blocked on a question it asked
  | 'compacting'
  | 'rate_limited'
  | 'dead'
  | 'unknown';

export interface RawMailbox {
  count: number;
  senders: string[];
  recent_count: number;
  recent_senders: string[];
}

export interface RawTask {
  id: string;
  status: string;
  dispatched_at: string;
  completed_at: string | null;
}

export interface RawDispatchEntry {
  label: string;
  started_at: string;
}

export interface RawWorker {
  target: string;
  process: string;
  window_name: string;
  cwd: string;
  pane_pid: string;
  state: RawWorkerState;
  detail: string;
  name: string;
  role: string;
  external: boolean;
  kind: 'discovered' | 'registered';
  host: string;
  /** unix epoch seconds (float) */
  host_last_seen: number;
  pending_mailbox: RawMailbox | null;
  recent_tasks: RawTask[];
  last_activity_age: string;
  incoming_dispatches: RawDispatchEntry[];
  outgoing_dispatches: RawDispatchEntry[];
  team_name: string;
  is_orchestrator: boolean;
  /**
   * Worker name of whoever spawned this one (registry WORKER_<sn>_SPAWNED_BY,
   * recorded by spawn-worker.sh from $EE_WORKER_NAME / --spawned-by).
   * Empty/absent for legacy entries, invited panes, and human-spawned workers.
   */
  spawned_by?: string;
  /**
   * True when an attached tmux client is currently viewing this pane.
   * Used to suppress OS push for the pane the user is actively looking at /
   * typing in. Optional — legacy agents predating the field omit it (→ false).
   */
  focused?: boolean;
  /**
   * Unix epoch seconds of the last busy→idle transition the hub observed.
   * Absent until the worker finishes its first turn after hub start.
   */
  finished_at?: number;
  /**
   * Unix epoch seconds when the user last demonstrably saw this worker:
   * focused tmux pane, detail-panel open, or explicit ack (POST /api/ack).
   * `finished_at > acked_at` ⇒ there is a result the user hasn't reviewed.
   */
  acked_at?: number;
  /**
   * Terminal tail — last N lines of stdout/stderr from the worker pane.
   *
   * Phase 3a uses this to render the panel's Recent Output section
   * against dummy data. The hub currently doesn't populate this field;
   * Phase 3b will add a backend endpoint (likely the same `/api/status`
   * or a dedicated `/api/worker/<id>/output`). Naming/typing here is a
   * forward guess and will be reconciled in 3b.
   */
  recent_output?: string[];
}

/** How long a team has gone without any activity. See lib/team_index.py. */
export type RawTeamTier = 'live' | 'recent' | 'dormant' | 'stale' | 'empty';

/**
 * One row of the hub's team index — every team state dir on the host, not just
 * the ones with workers reporting right now. A team with no live workers still
 * appears here, which is what makes cleanup candidates visible at all.
 */
export interface RawTeam {
  name: string;
  /** Host that owns this state dir. A team name is only unique within a host,
   *  so name alone cannot identify a team across the fleet. */
  host: string;
  state_dir: string;
  /** Registered workers in workers-runtime.env (not necessarily running). */
  workers: number;
  last_activity_at: string | null;
  /** Which signal was newest: 'dispatch' | 'heartbeat' | 'registry' | 'mailbox' */
  last_activity_source: string | null;
  idle_seconds: number | null;
  tier: RawTeamTier;
  size_bytes: number;
}

export interface RawStatusResponse {
  session_name: string;
  state_dir: string;
  workers: RawWorker[];
  /** Optional — hubs predating the team index omit it. */
  teams?: RawTeam[];
  /** ISO timestamp — reference time for host-stale calculations */
  ts: string;
}
