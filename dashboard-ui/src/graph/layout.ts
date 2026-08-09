/**
 * Graph view — pure tree layout over the domain `Host[]` snapshot.
 *
 * Shape per host:
 *   host ── team parent ── workers
 * where "team parent" is the team's orchestrator worker when one exists
 * (matching the hermes hierarchy) and a synthetic team node otherwise.
 *
 * Coordinates are world-space pixels; `x` grows by depth (COL_W), `y` is
 * the node's vertical center. Leaves take consecutive rows; parents sit at
 * the mean of their children. Pure — the renderer owns camera/animation.
 */

import type { Host, Worker } from '../types/domain';

export type GraphNodeKind = 'host' | 'team' | 'worker';

export interface GraphNode {
  /** `worker.id` for workers (click → selectWorker); synthetic for host/team. */
  id: string;
  kind: GraphNodeKind;
  label: string;
  sub: string;
  parentId: string | null;
  worker?: Worker;
  /**
   * Set on whichever node stands for a team — the synthetic team node, or the
   * orchestrator worker that replaces it. Carried as fields rather than parsed
   * back out of the synthetic id, which would guess wrong on any host or team
   * name containing the separator.
   */
  team?: { host: string; name: string };
  x: number;
  /** Vertical center. */
  y: number;
  w: number;
  h: number;
}

export const COL_W = 320;
export const ROW_H = 78;
const TEAM_GAP_ROWS = 0.5;
const HOST_GAP_ROWS = 1;

const SIZE: Record<GraphNodeKind, readonly [number, number]> = {
  host: [200, 58],
  team: [200, 54],
  worker: [224, 56],
};

function mk(
  kind: GraphNodeKind,
  id: string,
  label: string,
  sub: string,
  parentId: string | null,
  depth: number
): GraphNode {
  const [w, h] = SIZE[kind];
  return { id, kind, label, sub, parentId, x: depth * COL_W, y: 0, w, h };
}

export function buildGraph(hosts: Host[]): GraphNode[] {
  const nodes: GraphNode[] = [];
  let row = 0;

  hosts.forEach((host, hi) => {
    if (hi > 0) row += HOST_GAP_ROWS;
    const hostNode = mk(
      'host',
      `host:${host.id}`,
      host.id,
      `${host.teams.length} team${host.teams.length === 1 ? '' : 's'}`,
      null,
      0
    );
    nodes.push(hostNode);
    const teamYs: number[] = [];

    host.teams.forEach((team, ti) => {
      if (ti > 0) row += TEAM_GAP_ROWS;
      const orch = team.workers.find((w) => w.isOrchestrator);
      const parent = orch
        ? mk('worker', orch.id, orch.name, orch.role, hostNode.id, 1)
        : mk(
            'team',
            `team:${host.id}/${team.name}`,
            team.name,
            `${team.workers.length} worker${team.workers.length === 1 ? '' : 's'}`,
            hostNode.id,
            1
          );
      if (orch) parent.worker = orch;
      parent.team = { host: host.id, name: team.name };
      nodes.push(parent);

      const members = team.workers.filter((w) => w !== orch);
      const childYs: number[] = [];
      for (const w of members) {
        const n = mk('worker', w.id, w.name, w.role, parent.id, 2);
        n.worker = w;
        n.y = row * ROW_H;
        childYs.push(n.y);
        nodes.push(n);
        row += 1;
      }
      if (childYs.length) {
        parent.y = (childYs[0]! + childYs[childYs.length - 1]!) / 2;
      } else {
        parent.y = row * ROW_H;
        row += 1;
      }
      teamYs.push(parent.y);
    });

    if (teamYs.length) {
      hostNode.y = (teamYs[0]! + teamYs[teamYs.length - 1]!) / 2;
    } else {
      hostNode.y = row * ROW_H;
      row += 1;
    }
  });

  return nodes;
}

/**
 * Stable signature of the tree topology (ids + parent links, not states).
 * The view refits the camera only when this changes, so state churn from
 * polling never yanks the viewport around.
 */
export function topologyKey(nodes: GraphNode[]): string {
  return nodes.map((n) => `${n.id}>${n.parentId ?? ''}`).join('|');
}

/**
 * Whether a "spawned by" lineage link should be drawn from `src` to `node`.
 *
 * The link exists to show a relation the tree cannot: an orchestrator spawned
 * by something that lives elsewhere, e.g. hermes driving orchestrators that
 * each sit in their own team. Where the tree already expresses the relation,
 * a second edge is noise drawn across the cards.
 *
 * Suppressed when the spawner IS the tree parent, and when it is a sibling
 * under the same parent — a team node and its orchestrator worker often share
 * a name, so a worker spawned by its own team's orchestrator would otherwise
 * get a link doubling back over the group it is already drawn inside.
 */
export function shouldDrawLineage(node: GraphNode, src: GraphNode | undefined): boolean {
  if (!src || src === node) return false;
  if (src.id === node.parentId) return false;
  if (node.parentId !== null && src.parentId === node.parentId) return false;
  return true;
}
