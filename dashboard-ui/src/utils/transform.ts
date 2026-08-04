import type {
  Host,
  InactiveTeam,
  Team,
  TeamActivity,
  Worker,
  WorkerState,
} from '../types/domain';
import type {
  RawStatusResponse,
  RawTeam,
  RawWorker,
  RawWorkerState,
} from '../types/raw';

/** Section 13 decision — the two-step distinction (stale → dead) is a later phase. */
export const HOST_STALE_MS = 15_000;

/** Team identity across the fleet. Names repeat between hosts. */
function teamKey(host: string, team: string): string {
  return `${host}\u0000${displayTeam(team)}`;
}

function toActivity(t: RawTeam): TeamActivity {
  return {
    tier: t.tier,
    idleSeconds: t.idle_seconds,
    source: t.last_activity_source,
    registeredWorkers: t.workers,
    sizeBytes: t.size_bytes,
  };
}

/**
 * Teams the hub knows about that have no worker reporting right now.
 *
 * These are invisible in the host→team→worker tree by construction: it is built
 * from `raw.workers`, so a team nobody is running simply has no rows. That is
 * exactly the set worth showing as cleanup candidates, so it is derived
 * separately rather than folded into the tree.
 *
 * Sorted longest-idle first; teams with no activity signal at all sort last.
 */
export function inactiveTeams(raw: RawStatusResponse): InactiveTeam[] {
  if (!raw.teams) return [];
  // Keyed by host too: a `voc` running on one machine says nothing about an
  // abandoned `voc` on another, and both can be in this list at once.
  const active = new Set(
    raw.workers.map((w) => teamKey(w.host, w.team_name))
  );
  return raw.teams
    .filter((t) => !active.has(teamKey(t.host, t.name)))
    .map((t) => ({
      name: t.name,
      host: t.host,
      stateDir: t.state_dir,
      ...toActivity(t),
    }))
    .sort((a, b) => (b.idleSeconds ?? -1) - (a.idleSeconds ?? -1));
}

/**
 * Convert hub `/api/status` response into the UI domain tree.
 * Phase 3 integration: `setRawStatus(await fetch('/api/status').then(r => r.json()))`
 *
 * Notes:
 *   - Iteration order of `byHost` and `bucket.teams` preserves first-seen
 *     order from `raw.workers` (Map > plain object → no numeric-string
 *     auto-sort surprises like "1" jumping before "myteam").
 *   - Stale host (lastPingMs > HOST_STALE_MS) forces every worker inside
 *     to `dead` so sort/visuals match design intent without needing
 *     hostStale-aware sort logic.
 */
export function transformRawStatus(raw: RawStatusResponse): Host[] {
  const refTs = new Date(raw.ts).getTime();
  const activityByTeam = new Map(
    (raw.teams ?? []).map((t) => [teamKey(t.host, t.name), toActivity(t)])
  );
  type Bucket = { teams: Map<string, Worker[]>; lastSeenMs: number };
  const byHost: Map<string, Bucket> = new Map();

  for (const rw of raw.workers) {
    const teamName = displayTeam(rw.team_name);
    const lastSeenMs = refTs - rw.host_last_seen * 1000;

    let bucket = byHost.get(rw.host);
    if (!bucket) {
      bucket = { teams: new Map(), lastSeenMs };
      byHost.set(rw.host, bucket);
    }
    bucket.lastSeenMs = lastSeenMs;
    let teamWorkers = bucket.teams.get(teamName);
    if (!teamWorkers) {
      teamWorkers = [];
      bucket.teams.set(teamName, teamWorkers);
    }
    teamWorkers.push(rawToWorker(rw, refTs));
  }

  return Array.from(byHost.entries()).map(([hostId, bucket]) => {
    const lastPingMs = Math.max(0, bucket.lastSeenMs);
    const stale = lastPingMs > HOST_STALE_MS;
    return {
      id: hostId,
      lastPingMs,
      hiddenCount: 0, // computed in Phase 3 once the 5-min dead timer lands
      teams: Array.from(bucket.teams.entries()).map(
        ([name, workers]): Team => ({
          name,
          activity: activityByTeam.get(teamKey(hostId, name)),
          workers: stale
            ? workers.map((w) => ({ ...w, state: 'dead' as WorkerState }))
            : workers,
        })
      ),
    };
  });
}

/** Tmux target shape: `session:window[.pane]` (e.g. `1:1.1`, `dashboard:1.1`). */
const TARGET_SHAPED = /^[^:\s]+:\d+(?:\.\d+)?$/;

/** Disambiguator suffix used when a cwd-derived name would otherwise
 *  collide across multiple workers in the same directory. Uses the
 *  target's session part directly — session is unique per worker
 *  whether it's human-named ("dashboard", "voc") or a tmux index
 *  ("0", "1"). Window_name was tried as a tiebreaker but in practice
 *  carries garbage like "2.1.148" (the Claude Code build), so it's
 *  not used. */
function disambiguator(rw: RawWorker): string {
  const session = rw.target.split(':')[0] ?? '';
  return session || '?';
}

/** Fallback chain for the display name when hub `name` is the target itself.
 *  cwd-based fallback always appends a disambiguator so multiple workers
 *  living in the same directory (the common persona pattern) remain
 *  distinguishable. */
function displayName(rw: RawWorker): string {
  if (rw.name && rw.name !== rw.target && !TARGET_SHAPED.test(rw.name)) {
    return rw.name;
  }
  // cwd basename + disambiguator — gives "myrepo:dashboard", "myrepo:voc"
  // even when raw.name and target look identical.
  if (rw.cwd) {
    const base = rw.cwd.split('/').filter(Boolean).pop();
    if (base && base !== '/') return `${base}:${disambiguator(rw)}`;
  }
  // window_name fallback (skip the generic 'claude' default)
  if (rw.window_name && rw.window_name !== 'claude') return rw.window_name;
  // give up — show whatever raw had, even if it's just the target
  return rw.name || rw.target;
}

/** Fallback for numeric-only team names (tmux session indices). v2 round 3
 *  shortens the placeholder to `#N` for terminal-tool aesthetic and to
 *  keep the team label column narrow. */
function displayTeam(rawTeam: string): string {
  if (!rawTeam) return '(unknown)';
  if (/^\d+$/.test(rawTeam)) return `#${rawTeam}`;
  return rawTeam;
}

function rawToWorker(rw: RawWorker, refTs: number): Worker {
  return {
    id: `${rw.host}:${rw.target}`,
    name: displayName(rw),
    state: mapState(rw.state),
    role: rw.role,
    tmuxTarget: rw.target,
    process: rw.process,
    detail: rw.detail || undefined,
    isOrchestrator: rw.is_orchestrator,
    spawnedBy: rw.spawned_by || undefined,
    focused: rw.focused ?? false,
    enteredStateAt: new Date(refTs).toISOString(),
    dispatchOut: rw.outgoing_dispatches.length > 0,
    dispatchIn: rw.incoming_dispatches.length > 0,
    mailboxPending: rw.pending_mailbox?.count ?? 0,
    incomingDispatches: rw.incoming_dispatches,
    outgoingDispatches: rw.outgoing_dispatches,
    mailboxSenders: rw.pending_mailbox?.senders ?? [],
    recentTasks: rw.recent_tasks,
    recentOutput: rw.recent_output,
  };
}

function mapState(s: RawWorkerState): WorkerState {
  if (s === 'typed') return 'awaiting_user:typed';
  // The backend reports what is blocking the worker; the domain namespaces all
  // three under awaiting_user:* so sorting and notifications treat them alike.
  if (s === 'awaiting_permission') return 'awaiting_user:permission';
  if (s === 'awaiting_user') return 'awaiting_user:question';
  return s;
}

export function isHostStale(host: Host): boolean {
  return host.lastPingMs > HOST_STALE_MS;
}
