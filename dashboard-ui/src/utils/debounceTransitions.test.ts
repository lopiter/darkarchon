import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Worker } from '../types/domain';
import type { Transition } from './diffWorkers';
import {
  createTransitionDebouncer,
  type PushItem,
} from './debounceTransitions';

function w(id: string): Worker {
  return {
    id,
    name: id,
    state: 'awaiting_user:typed',
    role: '',
    tmuxTarget: `${id}:1`,
    process: 'claude',
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
  };
}

function awaiting(id: string): Transition {
  return { kind: 'awaiting', worker: w(id), host: 'h', team: 't' };
}

function rateLimited(id: string): Transition {
  return { kind: 'rate_limited', worker: w(id), host: 'h', team: 't' };
}

function mailbox(id: string): Transition {
  return {
    kind: 'mailbox_new',
    worker: w(id),
    host: 'h',
    team: 't',
    delta: 1,
  };
}

function dispatchOut(id: string): Transition {
  return { kind: 'dispatch_out', worker: w(id), host: 'h', team: 't' };
}

describe('createTransitionDebouncer', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('flushes after windowMs elapses', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([awaiting('a')]);
    expect(onFlush).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2999);
    expect(onFlush).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush.mock.calls[0]![0]!.awaiting).toHaveLength(1);
  });

  it('merges additional pushes within the window into one flush', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([awaiting('a')]);
    vi.advanceTimersByTime(1000);
    d.push([awaiting('b')]);
    vi.advanceTimersByTime(1000);
    d.push([awaiting('c'), rateLimited('d')]);
    vi.advanceTimersByTime(1000);
    expect(onFlush).toHaveBeenCalledTimes(1);
    const batch = onFlush.mock.calls[0]![0]!;
    expect(batch.awaiting.map((t) => (t as { worker: Worker }).worker.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
    expect(batch.rate_limited).toHaveLength(1);
  });

  it('starts a fresh window after the previous one fires', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([awaiting('a')]);
    vi.advanceTimersByTime(3000);
    expect(onFlush).toHaveBeenCalledTimes(1);

    d.push([awaiting('b')]);
    vi.advanceTimersByTime(3000);
    expect(onFlush).toHaveBeenCalledTimes(2);
    expect(onFlush.mock.calls[1]![0]!.awaiting).toHaveLength(1);
  });

  it('ignores mailbox/dispatch transitions (not pushable)', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([mailbox('a'), dispatchOut('b')]);
    vi.advanceTimersByTime(3000);
    expect(onFlush).not.toHaveBeenCalled();
  });

  it('only-non-pushable push does not start the timer', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([mailbox('a')]);
    // Then a real awaiting → new window starts now (not earlier).
    vi.advanceTimersByTime(2000);
    d.push([awaiting('b')]);
    vi.advanceTimersByTime(2999);
    expect(onFlush).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onFlush).toHaveBeenCalledTimes(1);
  });

  it('cancel() drops the buffer and prevents flush', () => {
    const onFlush = vi.fn<(b: PushItem) => void>();
    const d = createTransitionDebouncer(3000, onFlush);
    d.push([awaiting('a'), awaiting('b')]);
    d.cancel();
    vi.advanceTimersByTime(5000);
    expect(onFlush).not.toHaveBeenCalled();

    // And a fresh push afterwards still works (cancel is not terminal).
    d.push([awaiting('c')]);
    vi.advanceTimersByTime(3000);
    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush.mock.calls[0]![0]!.awaiting).toHaveLength(1);
  });
});
