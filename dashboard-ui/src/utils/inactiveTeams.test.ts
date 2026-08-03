import { describe, expect, it } from 'vitest';
import type { RawStatusResponse, RawTeam, RawWorker } from '../types/raw';
import { inactiveTeams, transformRawStatus } from './transform';

const TS = '2026-05-23T15:00:00Z';
const tsEpoch = new Date(TS).getTime() / 1000;

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
    kind: 'registered',
    host: 'h',
    host_last_seen: tsEpoch - 3,
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

function team(overrides: Partial<RawTeam>): RawTeam {
  return {
    name: 'someteam',
    state_dir: `/s/${overrides.name ?? 'someteam'}`,
    workers: 1,
    last_activity_at: '2026-05-01T00:00:00Z',
    last_activity_source: 'dispatch',
    idle_seconds: 10 * 86400,
    tier: 'dormant',
    size_bytes: 1024,
    ...overrides,
  };
}

function res(workers: RawWorker[], teams?: RawTeam[]): RawStatusResponse {
  return { session_name: 'myteam', state_dir: '/x', ts: TS, workers, teams };
}

describe('inactiveTeams', () => {
  it('returns teams that have no worker reporting', () => {
    const raw = res(
      [rw({ team_name: 'alive', target: 'a:1' })],
      [
        team({ name: 'alive', tier: 'live', idle_seconds: 0 }),
        team({ name: 'quiet', idle_seconds: 20 * 86400 }),
        team({ name: 'ancient', tier: 'stale', idle_seconds: 90 * 86400 }),
      ]
    );

    expect(inactiveTeams(raw).map((t) => t.name)).toEqual(['ancient', 'quiet']);
  });

  it('sorts longest-idle first and puts never-active teams last', () => {
    const raw = res(
      [],
      [
        team({ name: 'b', idle_seconds: 20 * 86400 }),
        team({ name: 'never', idle_seconds: null, last_activity_source: null }),
        team({ name: 'a', idle_seconds: 60 * 86400 }),
      ]
    );

    expect(inactiveTeams(raw).map((t) => t.name)).toEqual(['a', 'b', 'never']);
  });

  it('is empty when the hub predates the team index', () => {
    expect(inactiveTeams(res([rw({})]))).toEqual([]);
  });

  it('matches the display name so a numeric team is not double-counted', () => {
    // transformRawStatus renders team '3' as '#3'; the filter must compare
    // like for like or the team shows up as both active and inactive.
    const raw = res(
      [rw({ team_name: '3', target: 'a:1' })],
      [team({ name: '3', tier: 'live', idle_seconds: 0 })]
    );

    expect(inactiveTeams(raw)).toEqual([]);
  });
});

describe('transformRawStatus team activity', () => {
  it('attaches activity to the matching team', () => {
    const raw = res(
      [rw({ team_name: 'alpha', target: 'a:1' })],
      [team({ name: 'alpha', tier: 'dormant', idle_seconds: 9 * 86400, workers: 4 })]
    );

    const t = transformRawStatus(raw)[0]!.teams[0]!;
    expect(t.activity).toEqual({
      tier: 'dormant',
      idleSeconds: 9 * 86400,
      source: 'dispatch',
      registeredWorkers: 4,
      sizeBytes: 1024,
    });
  });

  it('leaves activity undefined when the hub sends no teams', () => {
    const t = transformRawStatus(res([rw({ team_name: 'alpha' })]))[0]!.teams[0]!;
    expect(t.activity).toBeUndefined();
  });
});
