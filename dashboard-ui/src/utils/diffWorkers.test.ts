import { describe, expect, it } from 'vitest';
import type { Host, Worker, WorkerState } from '../types/domain';
import { diffWorkers } from './diffWorkers';

function w(
  id: string,
  state: WorkerState,
  overrides: Partial<Worker> = {}
): Worker {
  return {
    id,
    name: id,
    state,
    role: '',
    tmuxTarget: `${id}:1`,
    process: 'claude',
    external: false,
    isOrchestrator: false,
    unseenDone: false,
    enteredStateAt: '2026-05-23T15:00:00Z',
    dispatchOut: false,
    dispatchIn: false,
    mailboxPending: 0,
    incomingDispatches: [],
    outgoingDispatches: [],
    mailboxSenders: [],
    recentTasks: [],
    ...overrides,
  };
}

function host(id: string, teamName: string, workers: Worker[]): Host {
  return {
    id,
    lastPingMs: 0,
    hiddenCount: 0,
    teams: [{ name: teamName, workers }],
  };
}

describe('diffWorkers', () => {
  it('returns empty when prev === next (no changes)', () => {
    const h = [host('m', 'T', [w('a', 'idle'), w('b', 'busy')])];
    expect(diffWorkers(h, h)).toEqual([]);
  });

  it('first snapshot — every worker is worker_added (no awaiting/idle emitted)', () => {
    const next = [
      host('m', 'T', [w('a', 'awaiting_user:typed'), w('b', 'busy')]),
    ];
    const result = diffWorkers([], next);
    expect(result.map((t) => t.kind).sort()).toEqual([
      'worker_added',
      'worker_added',
    ]);
  });

  it('busy → permission also emits awaiting', () => {
    // The notification exists to say "a human is needed"; a permission prompt
    // qualifies, so it must raise the same alert as the other awaiting states.
    const prev = [host('m', 'T', [w('a', 'busy')])];
    const next = [host('m', 'T', [w('a', 'awaiting_user:permission')])];
    const result = diffWorkers(prev, next);
    expect(result.map((t) => t.kind)).toEqual(['awaiting']);
  });

  it('busy → typed emits awaiting (and nothing else)', () => {
    const prev = [host('m', 'T', [w('a', 'busy')])];
    const next = [host('m', 'T', [w('a', 'awaiting_user:typed')])];
    const result = diffWorkers(prev, next);
    expect(result).toHaveLength(1);
    expect(result[0]!.kind).toBe('awaiting');
  });

  it('typed → question does not re-emit awaiting (still in awaiting category)', () => {
    const prev = [host('m', 'T', [w('a', 'awaiting_user:typed')])];
    const next = [host('m', 'T', [w('a', 'awaiting_user:question')])];
    expect(diffWorkers(prev, next)).toEqual([]);
  });

  it('busy → rate_limited emits rate_limited', () => {
    const prev = [host('m', 'T', [w('a', 'busy')])];
    const next = [host('m', 'T', [w('a', 'rate_limited')])];
    const result = diffWorkers(prev, next);
    expect(result.map((t) => t.kind)).toEqual(['rate_limited']);
  });

  it('busy → dead emits dead', () => {
    const prev = [host('m', 'T', [w('a', 'busy')])];
    const next = [host('m', 'T', [w('a', 'dead')])];
    expect(diffWorkers(prev, next).map((t) => t.kind)).toEqual(['dead']);
  });

  it('dead → idle does NOT emit idle (resurrection from gone state)', () => {
    const prev = [host('m', 'T', [w('a', 'dead')])];
    const next = [host('m', 'T', [w('a', 'idle')])];
    expect(diffWorkers(prev, next)).toEqual([]);
  });

  it('busy → idle emits idle', () => {
    const prev = [host('m', 'T', [w('a', 'busy')])];
    const next = [host('m', 'T', [w('a', 'idle')])];
    expect(diffWorkers(prev, next).map((t) => t.kind)).toEqual(['idle']);
  });

  it('worker disappears → worker_removed', () => {
    const prev = [host('m', 'T', [w('a', 'idle'), w('b', 'idle')])];
    const next = [host('m', 'T', [w('a', 'idle')])];
    const result = diffWorkers(prev, next);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ kind: 'worker_removed', workerId: 'b' });
  });

  it('dispatchOut false → true emits dispatch_out edge only', () => {
    const prev = [host('m', 'T', [w('a', 'busy', { dispatchOut: false })])];
    const next = [host('m', 'T', [w('a', 'busy', { dispatchOut: true })])];
    expect(diffWorkers(prev, next).map((t) => t.kind)).toEqual([
      'dispatch_out',
    ]);
  });

  it('dispatchOut true → true (still active) emits nothing', () => {
    const prev = [host('m', 'T', [w('a', 'busy', { dispatchOut: true })])];
    const next = [host('m', 'T', [w('a', 'busy', { dispatchOut: true })])];
    expect(diffWorkers(prev, next)).toEqual([]);
  });

  it('mailbox 0 → 2 emits mailbox_new with delta=2', () => {
    const prev = [host('m', 'T', [w('a', 'idle', { mailboxPending: 0 })])];
    const next = [host('m', 'T', [w('a', 'idle', { mailboxPending: 2 })])];
    const result = diffWorkers(prev, next);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ kind: 'mailbox_new', delta: 2 });
  });

  it('mailbox 3 → 1 (consumed) emits nothing — only growth triggers', () => {
    const prev = [host('m', 'T', [w('a', 'idle', { mailboxPending: 3 })])];
    const next = [host('m', 'T', [w('a', 'idle', { mailboxPending: 1 })])];
    expect(diffWorkers(prev, next)).toEqual([]);
  });

  it('combined: state change + mailbox grow + dispatch_in all on one worker', () => {
    const prev = [host('m', 'T', [w('a', 'busy', { mailboxPending: 0 })])];
    const next = [
      host('m', 'T', [
        w('a', 'awaiting_user:typed', {
          mailboxPending: 1,
          dispatchIn: true,
        }),
      ]),
    ];
    const kinds = diffWorkers(prev, next)
      .map((t) => t.kind)
      .sort();
    expect(kinds).toEqual(['awaiting', 'dispatch_in', 'mailbox_new']);
  });

  it('attaches host and team to each transition', () => {
    const prev = [host('m', 'T1', [w('a', 'busy')])];
    const next = [host('m', 'T1', [w('a', 'awaiting_user:typed')])];
    const [t] = diffWorkers(prev, next);
    expect(t).toMatchObject({ host: 'm', team: 'T1' });
  });
});
