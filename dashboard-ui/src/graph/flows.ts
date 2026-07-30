/**
 * Graph view — one-shot animation events derived from poll-to-poll
 * transitions (utils/diffWorkers). Continuous dispatch streams are NOT
 * produced here: the renderer draws those every frame straight from
 * `worker.dispatchIn`. This module only maps discrete transitions onto
 * tree edges / nodes.
 */

import type { Transition } from '../utils/diffWorkers';
import type { GraphNode } from './layout';

export interface FlowBurst {
  fromId: string;
  toId: string;
  /** reply: work finished, particles back to the parent. question: awaiting_user. */
  kind: 'reply' | 'question';
}

export interface GraphEvents {
  bursts: FlowBurst[];
  /** Mailbox badge bump targets. */
  bumps: string[];
  /** Attention ripples: amber = awaiting_user, red = rate_limited. */
  ripples: Array<{ id: string; kind: 'amber' | 'red' }>;
}

export function transitionsToEvents(
  transitions: Transition[],
  nodes: GraphNode[]
): GraphEvents {
  const parentOf = new Map(nodes.map((n) => [n.id, n.parentId]));
  const events: GraphEvents = { bursts: [], bumps: [], ripples: [] };

  for (const t of transitions) {
    if (t.kind === 'worker_removed') continue;
    const id = 'worker' in t ? t.worker.id : null;
    if (!id || !parentOf.has(id)) continue;
    const pid = parentOf.get(id) ?? null;

    switch (t.kind) {
      case 'idle':
        if (pid) events.bursts.push({ fromId: id, toId: pid, kind: 'reply' });
        break;
      case 'awaiting':
        if (pid) events.bursts.push({ fromId: id, toId: pid, kind: 'question' });
        events.ripples.push({ id, kind: 'amber' });
        break;
      case 'rate_limited':
        events.ripples.push({ id, kind: 'red' });
        break;
      case 'mailbox_new':
        events.bumps.push(id);
        break;
      default:
        break;
    }
  }
  return events;
}
