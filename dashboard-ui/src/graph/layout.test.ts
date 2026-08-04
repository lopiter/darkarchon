import { describe, expect, it } from 'vitest';
import type { Host, Worker, WorkerState } from '../types/domain';
import { buildGraph, shouldDrawLineage, topologyKey, type GraphNode } from './layout';

function mkWorker(
  id: string,
  overrides: Partial<Worker> = {}
): Worker {
  return {
    id,
    name: id,
    state: 'idle' as WorkerState,
    role: 'dev',
    tmuxTarget: `${id}:1.1`,
    process: 'claude',
    isOrchestrator: false,
    focused: false,
    unseenDone: false,
    enteredStateAt: '2026-01-01T00:00:00.000Z',
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

function mkHost(id: string, teams: Host['teams']): Host {
  return { id, teams, lastPingMs: 0, hiddenCount: 0 };
}

describe('buildGraph', () => {
  it('promotes the orchestrator to team parent and hangs workers off it', () => {
    const orch = mkWorker('h:orch', { isOrchestrator: true });
    const a = mkWorker('h:a');
    const b = mkWorker('h:b');
    const nodes = buildGraph([
      mkHost('h', [{ name: 'TEAM', workers: [orch, a, b] }]),
    ]);

    const byId = new Map(nodes.map((n) => [n.id, n]));
    expect(byId.get('h:a')!.parentId).toBe('h:orch');
    expect(byId.get('h:b')!.parentId).toBe('h:orch');
    expect(byId.get('h:orch')!.parentId).toBe('host:h');
    expect(byId.get('host:h')!.parentId).toBeNull();
    // no synthetic team node when an orchestrator exists
    expect(nodes.some((n) => n.kind === 'team')).toBe(false);
    // orchestrator sits at the vertical center of its children
    expect(byId.get('h:orch')!.y).toBe(
      (byId.get('h:a')!.y + byId.get('h:b')!.y) / 2
    );
  });

  it('falls back to a synthetic team node when no orchestrator exists', () => {
    const nodes = buildGraph([
      mkHost('h', [{ name: 'TEAM', workers: [mkWorker('h:a')] }]),
    ]);
    const team = nodes.find((n) => n.kind === 'team');
    expect(team).toBeDefined();
    expect(team!.id).toBe('team:h/TEAM');
    expect(nodes.find((n) => n.id === 'h:a')!.parentId).toBe(team!.id);
  });

  it('keeps depths in distinct columns and rows non-overlapping', () => {
    const orch = mkWorker('h:orch', { isOrchestrator: true });
    const nodes = buildGraph([
      mkHost('h', [
        { name: 'A', workers: [orch, mkWorker('h:a1'), mkWorker('h:a2')] },
        { name: 'B', workers: [mkWorker('h:b1'), mkWorker('h:b2')] },
      ]),
    ]);
    const host = nodes.find((n) => n.kind === 'host')!;
    const workers = nodes.filter((n) => n.parentId && n.parentId !== host.id);
    // leaves occupy distinct y rows
    const ys = workers.map((n) => n.y);
    expect(new Set(ys).size).toBe(ys.length);
    // depth columns: host < parents < leaves
    for (const n of workers) {
      const parent = nodes.find((p) => p.id === n.parentId)!;
      expect(n.x).toBeGreaterThan(parent.x);
      expect(parent.x).toBeGreaterThan(host.x);
    }
  });

  it('topologyKey ignores state churn but tracks membership', () => {
    const host = (state: WorkerState, extra = false) =>
      mkHost('h', [
        {
          name: 'TEAM',
          workers: [
            mkWorker('h:a', { state }),
            ...(extra ? [mkWorker('h:b')] : []),
          ],
        },
      ]);
    expect(topologyKey(buildGraph([host('idle')]))).toBe(
      topologyKey(buildGraph([host('busy')]))
    );
    expect(topologyKey(buildGraph([host('idle')]))).not.toBe(
      topologyKey(buildGraph([host('idle', true)]))
    );
  });
});

describe('shouldDrawLineage', () => {
  const node = (id: string, parentId: string | null): GraphNode =>
    ({ id, kind: 'worker', label: id, sub: '', parentId, x: 0, y: 0, w: 0, h: 0 });

  it('draws a link to a spawner in another team', () => {
    // The case the link exists for: hermes drives orchestrators that each
    // live in their own team, which the tree cannot show.
    const orch = node('orch', 'team:voc');
    const hermes = node('hermes', 'team:fleet');
    expect(shouldDrawLineage(orch, hermes)).toBe(true);
  });

  it('skips a spawner that is already the tree parent', () => {
    const worker = node('w', 'team:voc');
    expect(shouldDrawLineage(worker, node('team:voc', 'host:a'))).toBe(false);
  });

  it('skips a sibling spawner under the same team', () => {
    // A team node and its orchestrator worker often share a name, so a worker
    // spawned by its own team's orchestrator resolved to the sibling and got a
    // dashed link doubling back across the group it already sits in.
    const websiteUi = node('website-ui', 'team:voc-2');
    const orchestrator = node('voc-2', 'team:voc-2');
    expect(shouldDrawLineage(websiteUi, orchestrator)).toBe(false);
  });

  it('skips an unresolved or self spawner', () => {
    const w = node('w', 'team:voc');
    expect(shouldDrawLineage(w, undefined)).toBe(false);
    expect(shouldDrawLineage(w, w)).toBe(false);
  });

  it('still links two parentless roots', () => {
    expect(shouldDrawLineage(node('a', null), node('b', null))).toBe(true);
  });
});
