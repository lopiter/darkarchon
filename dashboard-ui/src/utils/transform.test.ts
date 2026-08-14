import { describe, expect, it } from 'vitest';
import type { RawStatusResponse, RawWorker } from '../types/raw';
import { HOST_STALE_MS, isHostStale, transformRawStatus } from './transform';

const TS = '2026-05-23T15:00:00Z';
const tsEpoch = new Date(TS).getTime() / 1000;
const secAgo = (s: number) => tsEpoch - s;

function rw(overrides: Partial<RawWorker>): RawWorker {
  return {
    target: 'x:1.1',
    process: 'claude',
    window_name: 'claude',
    cwd: '/x',
    pane_pid: '1',
    state: 'idle',
    detail: '',
    name: 'w',
    role: '',
    external: false,
    kind: 'discovered',
    host: 'h',
    host_last_seen: secAgo(3),
    pending_mailbox: null,
    recent_tasks: [],
    last_activity_age: '—',
    incoming_dispatches: [],
    outgoing_dispatches: [],
    team_name: 't',
    is_orchestrator: false,
    ...overrides,
  };
}

function res(workers: RawWorker[]): RawStatusResponse {
  return {
    session_name: 'myteam',
    state_dir: '/x',
    ts: TS,
    workers,
  };
}

describe('transformRawStatus', () => {
  it('maps spawned_by to spawnedBy, absent/empty → undefined', () => {
    const hosts = transformRawStatus(
      res([
        rw({ name: 'w1', target: 'a:1', spawned_by: 'hermes' }),
        rw({ name: 'w2', target: 'a:2', spawned_by: '' }),
        rw({ name: 'w3', target: 'a:3' }),
      ])
    );
    const workers = hosts[0]!.teams[0]!.workers;
    expect(workers.map((w) => w.spawnedBy)).toEqual(['hermes', undefined, undefined]);
  });

  it('maps peer_name to peerName, absent/empty → undefined', () => {
    const hosts = transformRawStatus(
      res([
        rw({ name: 'w1', target: 'a:1', peer_name: 'darkarchon-c3' }),
        rw({ name: 'w2', target: 'a:2', peer_name: '' }),
        rw({ name: 'w3', target: 'a:3' }),
      ])
    );
    const workers = hosts[0]!.teams[0]!.workers;
    expect(workers.map((w) => w.peerName)).toEqual(['darkarchon-c3', undefined, undefined]);
  });

  it('groups workers by host then by team_name', () => {
    const hosts = transformRawStatus(
      res([
        rw({ host: 'a', team_name: 'T1', name: 'w1', target: 'a:1' }),
        rw({ host: 'a', team_name: 'T1', name: 'w2', target: 'a:2' }),
        rw({ host: 'a', team_name: 'T2', name: 'w3', target: 'a:3' }),
        rw({ host: 'b', team_name: 'T1', name: 'w4', target: 'b:1' }),
      ])
    );
    expect(hosts).toHaveLength(2);
    const a = hosts.find((h) => h.id === 'a')!;
    expect(a.teams.map((t) => t.name).sort()).toEqual(['T1', 'T2']);
    const t1 = a.teams.find((t) => t.name === 'T1')!;
    expect(t1.workers.map((w) => w.name)).toEqual(['w1', 'w2']);
  });

  it('maps typed → awaiting_user:typed, keeps others 1:1', () => {
    const hosts = transformRawStatus(
      res([
        rw({ state: 'typed', name: 'a', target: ':1' }),
        rw({ state: 'busy', name: 'b', target: ':2' }),
        rw({ state: 'dead', name: 'c', target: ':3' }),
      ])
    );
    const states = hosts[0]!.teams[0]!.workers.map((w) => w.state);
    expect(states).toEqual(['awaiting_user:typed', 'busy', 'dead']);
  });

  it('namespaces the backend blocked states under awaiting_user:*', () => {
    // The resolver distinguishes a permission prompt from a question the worker
    // asked; both mean a human has to act, so both land under awaiting_user:.
    const hosts = transformRawStatus(
      res([
        rw({ state: 'awaiting_permission', name: 'a', target: ':1' }),
        rw({ state: 'awaiting_user', name: 'b', target: ':2' }),
      ])
    );
    const states = hosts[0]!.teams[0]!.workers.map((w) => w.state);
    expect(states).toEqual(['awaiting_user:permission', 'awaiting_user:question']);
  });

  it('derives unseenDone from finished_at > acked_at', () => {
    const workers = transformRawStatus(
      res([
        rw({ name: 'unseen', target: ':1', finished_at: secAgo(60) }),
        rw({
          name: 'acked',
          target: ':2',
          finished_at: secAgo(60),
          acked_at: secAgo(30),
        }),
        rw({ name: 'never-finished', target: ':3' }),
      ])
    )[0]!.teams[0]!.workers;
    expect(workers.map((w) => w.unseenDone)).toEqual([true, false, false]);
    expect(workers[0]!.finishedAtMs).toBe(secAgo(60) * 1000);
  });

  it('a dead worker never reports unseenDone', () => {
    const workers = transformRawStatus(
      res([rw({ name: 'gone', target: ':1', state: 'dead', finished_at: secAgo(60) })])
    )[0]!.teams[0]!.workers;
    expect(workers[0]!.unseenDone).toBe(false);
  });

  it('maps focused through, defaulting to false when the agent omits it', () => {
    const [host] = transformRawStatus(
      res([
        rw({ focused: true, name: 'viewed', target: ':1' }),
        rw({ focused: false, name: 'background', target: ':2' }),
        rw({ name: 'legacy', target: ':3' }), // agent predating the field
      ])
    );
    const byName = Object.fromEntries(
      host!.teams[0]!.workers.map((w) => [w.name, w.focused])
    );
    expect(byName).toEqual({ viewed: true, background: false, legacy: false });
  });

  it('coerces empty detail to undefined', () => {
    const [host] = transformRawStatus(res([rw({ detail: '' })]));
    expect(host!.teams[0]!.workers[0]!.detail).toBeUndefined();
  });

  it('builds stable id `${host}:${target}`', () => {
    const [host] = transformRawStatus(
      res([rw({ host: 'mac', target: 'myteam:2.1' })])
    );
    expect(host!.teams[0]!.workers[0]!.id).toBe('mac:myteam:2.1');
  });

  it('treats empty team_name as "(unknown)"', () => {
    const [host] = transformRawStatus(res([rw({ team_name: '' })]));
    expect(host!.teams[0]!.name).toBe('(unknown)');
  });

  it('prefixes numeric-only team_name as #N', () => {
    const [host] = transformRawStatus(res([rw({ team_name: '2' })]));
    expect(host!.teams[0]!.name).toBe('#2');
  });

  it('keeps a meaningful name as-is', () => {
    const [host] = transformRawStatus(
      res([rw({ name: 'writer', target: 'docu:1.1', cwd: '/x' })])
    );
    expect(host!.teams[0]!.workers[0]!.name).toBe('writer');
  });

  it('falls back to cwd basename with numeric-session disambiguator', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          name: '1:1.1',
          target: '1:1.1',
          cwd: '/Users/u/work/backend',
        }),
      ])
    );
    // session "1" is numeric → still kept as the disambiguator suffix.
    expect(host!.teams[0]!.workers[0]!.name).toBe('backend:1');
  });

  it('uses session name as disambiguator when session is human-named', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          name: 'dashboard:1.1',
          target: 'dashboard:1.1',
          cwd: '/Users/u/work/myrepo',
        }),
      ])
    );
    expect(host!.teams[0]!.workers[0]!.name).toBe('myrepo:dashboard');
  });

  it('disambiguates multiple workers in the same cwd', () => {
    const hosts = transformRawStatus(
      res([
        rw({
          name: 'dashboard:1.1',
          target: 'dashboard:1.1',
          cwd: '/Users/u/work/myrepo',
        }),
        rw({
          name: 'voc:1.1',
          target: 'voc:1.1',
          cwd: '/Users/u/work/myrepo',
        }),
      ])
    );
    const names = hosts[0]!.teams[0]!.workers.map((w) => w.name);
    expect(names).toEqual(['myrepo:dashboard', 'myrepo:voc']);
  });

  it('falls back to window_name when name=target and cwd is empty', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          name: '0:1.1',
          target: '0:1.1',
          cwd: '',
          window_name: 'orchestrator',
        }),
      ])
    );
    expect(host!.teams[0]!.workers[0]!.name).toBe('orchestrator');
  });

  it('does not pick window_name when it is the generic "claude"', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          name: '0:1.1',
          target: '0:1.1',
          cwd: '',
          window_name: 'claude',
        }),
      ])
    );
    // No better source → falls back to the raw name (target-shaped).
    expect(host!.teams[0]!.workers[0]!.name).toBe('0:1.1');
  });

  it('uses target session (numeric) as disambiguator — ignores window_name garbage', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          name: '0:1.1',
          target: '0:1.1',
          cwd: '/Users/u/work/myrepo',
          window_name: '2.1.148',
        }),
      ])
    );
    expect(host!.teams[0]!.workers[0]!.name).toBe('myrepo:0');
  });

  it('computes lastPingMs from (ts - host_last_seen) * 1000', () => {
    const [host] = transformRawStatus(
      res([rw({ host_last_seen: secAgo(5) })])
    );
    expect(host!.lastPingMs).toBeGreaterThanOrEqual(4500);
    expect(host!.lastPingMs).toBeLessThanOrEqual(5500);
  });

  it('clamps lastPingMs to 0 when host_last_seen is in the future', () => {
    const [host] = transformRawStatus(
      res([rw({ host_last_seen: tsEpoch + 60 })])
    );
    expect(host!.lastPingMs).toBe(0);
  });

  it('forces every worker to dead when host is stale (>15s)', () => {
    const hosts = transformRawStatus(
      res([
        rw({
          host: 'stale',
          host_last_seen: secAgo(120),
          state: 'idle',
          name: 'a',
          target: ':1',
        }),
        rw({
          host: 'stale',
          host_last_seen: secAgo(120),
          state: 'busy',
          name: 'b',
          target: ':2',
        }),
        rw({
          host: 'fresh',
          host_last_seen: secAgo(2),
          state: 'idle',
          name: 'c',
          target: ':3',
        }),
      ])
    );
    const stale = hosts.find((h) => h.id === 'stale')!;
    const fresh = hosts.find((h) => h.id === 'fresh')!;
    expect(stale.teams[0]!.workers.map((w) => w.state)).toEqual([
      'dead',
      'dead',
    ]);
    expect(fresh.teams[0]!.workers[0]!.state).toBe('idle');
  });

  it('maps tier-2 fields (dispatchOut, dispatchIn, mailboxPending)', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          outgoing_dispatches: [{ label: 'x', started_at: TS }],
          incoming_dispatches: [{ label: 'y', started_at: TS }],
          pending_mailbox: {
            count: 3,
            senders: ['a'],
            recent_count: 1,
            recent_senders: ['a'],
          },
        }),
      ])
    );
    const wk = host!.teams[0]!.workers[0]!;
    expect(wk.dispatchOut).toBe(true);
    expect(wk.dispatchIn).toBe(true);
    expect(wk.mailboxPending).toBe(3);
  });

  it('carries panel-only fields through (raw passthrough for DetailPanel)', () => {
    const [host] = transformRawStatus(
      res([
        rw({
          outgoing_dispatches: [{ label: 'dest', started_at: TS }],
          incoming_dispatches: [{ label: 'src', started_at: TS }],
          pending_mailbox: {
            count: 2,
            senders: ['alpha', 'beta'],
            recent_count: 1,
            recent_senders: ['alpha'],
          },
          recent_tasks: [
            { id: 't1', status: 'done', dispatched_at: TS, completed_at: TS },
          ],
        }),
      ])
    );
    const wk = host!.teams[0]!.workers[0]!;
    expect(wk.incomingDispatches).toHaveLength(1);
    expect(wk.outgoingDispatches[0]!.label).toBe('dest');
    expect(wk.mailboxSenders).toEqual(['alpha', 'beta']);
    expect(wk.recentTasks).toHaveLength(1);
    expect(wk.recentTasks[0]!.id).toBe('t1');
  });

  it('preserves first-seen insertion order for hosts and teams', () => {
    const hosts = transformRawStatus(
      res([
        rw({ host: 'second', team_name: 'myteam', target: ':1' }),
        rw({ host: 'second', team_name: 'scratch', target: ':2' }),
      ])
    );
    const sm = hosts.find((h) => h.id === 'second')!;
    expect(sm.teams.map((t) => t.name)).toEqual(['myteam', 'scratch']);
  });
});

describe('isHostStale', () => {
  it('returns true when lastPingMs > 15_000', () => {
    const stale = { lastPingMs: HOST_STALE_MS + 1 } as never;
    expect(isHostStale(stale)).toBe(true);
  });
  it('returns false when lastPingMs at threshold', () => {
    const fresh = { lastPingMs: HOST_STALE_MS } as never;
    expect(isHostStale(fresh)).toBe(false);
  });
});
