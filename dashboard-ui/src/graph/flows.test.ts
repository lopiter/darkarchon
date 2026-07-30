import { describe, expect, it } from 'vitest';
import type { Worker } from '../types/domain';
import type { Transition } from '../utils/diffWorkers';
import { transitionsToEvents } from './flows';
import type { GraphNode } from './layout';

const worker = (id: string): Worker => ({
  id,
  name: id,
  state: 'idle',
  role: 'dev',
  tmuxTarget: `${id}:1.1`,
  process: 'claude',
  isOrchestrator: false,
  focused: false,
  enteredStateAt: '2026-01-01T00:00:00.000Z',
  dispatchOut: false,
  dispatchIn: false,
  mailboxPending: 0,
  incomingDispatches: [],
  outgoingDispatches: [],
  mailboxSenders: [],
  recentTasks: [],
});

const node = (id: string, parentId: string | null): GraphNode => ({
  id,
  kind: 'worker',
  label: id,
  sub: 'dev',
  parentId,
  x: 0,
  y: 0,
  w: 224,
  h: 56,
});

const NODES = [node('orch', 'host:h'), node('w1', 'orch'), node('host:h', null)];

const tr = (kind: Transition['kind'], id: string): Transition =>
  ({ kind, worker: worker(id), host: 'h', team: 'TEAM' }) as Transition;

describe('transitionsToEvents', () => {
  it('maps idle transition to a reply burst toward the parent', () => {
    const { bursts } = transitionsToEvents([tr('idle', 'w1')], NODES);
    expect(bursts).toEqual([{ fromId: 'w1', toId: 'orch', kind: 'reply' }]);
  });

  it('maps awaiting to a question burst plus an amber ripple', () => {
    const { bursts, ripples } = transitionsToEvents([tr('awaiting', 'w1')], NODES);
    expect(bursts).toEqual([{ fromId: 'w1', toId: 'orch', kind: 'question' }]);
    expect(ripples).toEqual([{ id: 'w1', kind: 'amber' }]);
  });

  it('maps rate_limited to a red ripple and mailbox_new to a bump', () => {
    const events = transitionsToEvents(
      [
        tr('rate_limited', 'w1'),
        { kind: 'mailbox_new', worker: worker('orch'), host: 'h', team: 'TEAM', delta: 2 },
      ],
      NODES
    );
    expect(events.ripples).toEqual([{ id: 'w1', kind: 'red' }]);
    expect(events.bumps).toEqual(['orch']);
  });

  it('drops transitions for workers that are not in the graph', () => {
    const events = transitionsToEvents(
      [tr('idle', 'ghost'), { kind: 'worker_removed', workerId: 'w1', host: 'h', team: 'T' }],
      NODES
    );
    expect(events.bursts).toEqual([]);
    expect(events.bumps).toEqual([]);
    expect(events.ripples).toEqual([]);
  });
});
