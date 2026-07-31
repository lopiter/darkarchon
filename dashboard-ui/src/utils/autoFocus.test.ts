import { describe, expect, it } from 'vitest';
import type { Host, Worker, WorkerState } from '../types/domain';
import { initialAutoFocusState, nextAutoFocus, type AutoFocusState } from './autoFocus';
import { diffWorkers } from './diffWorkers';

function w(id: string, state: WorkerState, overrides: Partial<Worker> = {}): Worker {
  return {
    id,
    name: id,
    state,
    role: '',
    tmuxTarget: `${id}:1`,
    process: 'claude',
    isOrchestrator: false,
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

function host(id: string, workers: Worker[]): Host {
  return { id, lastPingMs: 0, hiddenCount: 0, teams: [{ name: 'T', workers }] };
}

/** Run one tick like GraphView does: diff prev→next, feed both in. */
function tick(
  state: AutoFocusState,
  prev: Host[],
  next: Host[]
): { state: AutoFocusState; focusId: string | null } {
  return nextAutoFocus(state, next, diffWorkers(prev, next));
}

describe('nextAutoFocus', () => {
  it('focuses a worker entering awaiting (P1)', () => {
    const prev = [host('m', [w('a', 'busy')])];
    const next = [host('m', [w('a', 'awaiting_user:permission')])];
    const r = tick(initialAutoFocusState(), prev, next);
    expect(r.focusId).toBe('a');
  });

  it('holds focus on the oldest awaiting worker when a second one appears', () => {
    const s0 = initialAutoFocusState();
    const h1 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'busy')])];
    const r1 = tick(s0, [host('m', [w('a', 'busy'), w('b', 'busy')])], h1);
    expect(r1.focusId).toBe('a');

    const h2 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'awaiting_user:question')])];
    const r2 = tick(r1.state, h1, h2);
    expect(r2.focusId).toBeNull(); // camera stays put — no ping-pong
    expect(r2.state.targetId).toBe('a');
  });

  it('advances to the next-oldest awaiting worker when the held one resolves', () => {
    const s0 = initialAutoFocusState();
    const h1 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'awaiting_user:question')])];
    const r1 = tick(s0, [host('m', [w('a', 'busy'), w('b', 'busy')])], h1);
    expect(r1.focusId).toBe('a');

    const h2 = [host('m', [w('a', 'busy'), w('b', 'awaiting_user:question')])];
    const r2 = tick(r1.state, h1, h2);
    expect(r2.focusId).toBe('b');
  });

  it('focuses a completion (P2) when nobody is awaiting', () => {
    const prev = [host('m', [w('a', 'busy')])];
    const next = [host('m', [w('a', 'idle')])];
    const r = tick(initialAutoFocusState(), prev, next);
    expect(r.focusId).toBe('a');
  });

  it('a completion never steals focus from an awaiting worker', () => {
    const s0 = initialAutoFocusState();
    const h1 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'busy')])];
    const r1 = tick(s0, [host('m', [w('a', 'busy'), w('b', 'busy')])], h1);
    expect(r1.focusId).toBe('a');

    const h2 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'idle')])];
    const r2 = tick(r1.state, h1, h2);
    expect(r2.focusId).toBeNull();
    expect(r2.state.targetId).toBe('a');
  });

  it('after the last awaiting resolves, a later completion takes focus', () => {
    const s0 = initialAutoFocusState();
    const h1 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'busy')])];
    const r1 = tick(s0, [host('m', [w('a', 'busy'), w('b', 'busy')])], h1);

    // a resolves to busy — nobody awaiting, nothing completed → camera stays.
    const h2 = [host('m', [w('a', 'busy'), w('b', 'busy')])];
    const r2 = tick(r1.state, h1, h2);
    expect(r2.focusId).toBeNull();

    // b finishes → P2 focus.
    const h3 = [host('m', [w('a', 'busy'), w('b', 'idle')])];
    const r3 = tick(r2.state, h2, h3);
    expect(r3.focusId).toBe('b');
  });

  it('re-entering awaiting after resolving re-focuses (fresh arrival order)', () => {
    const s0 = initialAutoFocusState();
    const busy = [host('m', [w('a', 'busy'), w('b', 'busy')])];
    const h1 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'awaiting_user:typed')])];
    const r1 = tick(s0, busy, h1);
    expect(r1.focusId).toBe('a');

    // a resolves, then comes back — b (older now) should hold, not a.
    const h2 = [host('m', [w('a', 'busy'), w('b', 'awaiting_user:typed')])];
    const r2 = tick(r1.state, h1, h2);
    expect(r2.focusId).toBe('b');

    const h3 = [host('m', [w('a', 'awaiting_user:typed'), w('b', 'awaiting_user:typed')])];
    const r3 = tick(r2.state, h2, h3);
    expect(r3.focusId).toBeNull();
    expect(r3.state.targetId).toBe('b');
  });

  it('does not re-emit the same target twice', () => {
    const prev = [host('m', [w('a', 'busy')])];
    const next = [host('m', [w('a', 'awaiting_user:typed')])];
    const r1 = tick(initialAutoFocusState(), prev, next);
    expect(r1.focusId).toBe('a');
    const r2 = tick(r1.state, next, next);
    expect(r2.focusId).toBeNull();
  });

  it('clears a completed target when its worker is removed from the graph', () => {
    const s0 = initialAutoFocusState();
    const h1 = [host('m', [w('a', 'busy')])];
    const h2 = [host('m', [w('a', 'idle')])];
    const r1 = tick(s0, h1, h2);
    expect(r1.focusId).toBe('a');

    const r2 = tick(r1.state, h2, [host('m', [])]);
    expect(r2.focusId).toBeNull();
    expect(r2.state.targetId).toBeNull();
  });
});
