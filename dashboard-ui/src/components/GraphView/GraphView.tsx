/**
 * Graph view — canvas tree of hosts → orchestrators → workers with live
 * dispatch streams. Alternative to the card Dashboard; toggled via the
 * store's `view` (ViewToggle pill).
 *
 * The React side stays thin: it feeds store snapshots into GraphRenderer
 * (layout + one-shot events from diffWorkers) and mirrors selection both
 * ways so clicking a node opens the same DetailPanel as the card view.
 */

import { useEffect, useRef, useState } from 'react';
import { transitionsToEvents } from '../../graph/flows';
import { buildGraph, type GraphNode } from '../../graph/layout';
import { GraphRenderer } from '../../graph/renderer';
import { useDashboardStore } from '../../store/dashboard';
import type { Host } from '../../types/domain';
import { initialAutoFocusState, nextAutoFocus } from '../../utils/autoFocus';
import { diffWorkers, type Transition } from '../../utils/diffWorkers';
import { isHostStale } from '../../utils/transform';
import { openContextMenu, type MenuRequest } from '../ContextMenu/ContextMenu';
import { teamMenuItems, workerMenuItems } from '../ContextMenu/menus';
import { EmptyState } from '../EmptyState/EmptyState';
import { NotificationToggle } from '../NotificationToggle/NotificationToggle';
import { ThemeToggle } from '../ThemeToggle/ThemeToggle';
import styles from './GraphView.module.css';

/**
 * Right-click menu for a graph node, resolved against the live store.
 *
 * A team-parent node carries `team` whether it is the synthetic team node or
 * the orchestrator standing in for it, so an orchestrator's menu holds its own
 * worker actions *and* a way into the team it fronts — otherwise a team with
 * an orchestrator would have no reachable team panel in this view at all.
 *
 * Host nodes have nothing to offer and return null, which leaves the browser's
 * own menu in place rather than opening an empty one.
 */
function graphNodeMenu(
  node: GraphNode,
  x: number,
  y: number
): MenuRequest | null {
  const { hosts } = useDashboardStore.getState();
  const host = node.team ? hosts.find((h) => h.id === node.team!.host) : undefined;
  const team = host?.teams.find((t) => t.name === node.team!.name);

  if (node.kind === 'team') {
    if (!host || !team) return null;
    return { x, y, title: `${team.name} · ${host.id}`, items: teamMenuItems(host.id, team) };
  }

  if (node.kind === 'worker') {
    // Re-read the worker from the store: `node.worker` is a snapshot from
    // whichever layout pass built it.
    for (const h of hosts) {
      for (const t of h.teams) {
        const w = t.workers.find((w) => w.id === node.id);
        if (!w) continue;
        const items = workerMenuItems(w);
        if (host && team) {
          items.push({
            label: `Open team panel · ${team.name}`,
            onSelect: () =>
              useDashboardStore.getState().selectTeam(host.id, team.name),
          });
        }
        return { x, y, title: w.name, items };
      }
    }
  }
  return null;
}

interface FeedItem {
  key: number;
  cls: 'dispatch' | 'done' | 'warn';
  text: string;
}

const FEED_MAX = 6;
let feedSeq = 0;

/** Human-readable one-liners for the bottom-left event feed. */
function transitionsToFeed(
  transitions: Transition[],
  nodes: GraphNode[]
): FeedItem[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const out: FeedItem[] = [];
  for (const t of transitions) {
    if (t.kind === 'worker_removed') continue;
    const name = t.worker.name;
    if (t.kind === 'dispatch_in') {
      const parentId = byId.get(t.worker.id)?.parentId;
      const parent = parentId ? byId.get(parentId)?.label : null;
      out.push({
        key: ++feedSeq,
        cls: 'dispatch',
        text: `${parent ? `${parent} ▸ ` : ''}${name} · dispatch`,
      });
    } else if (t.kind === 'idle') {
      out.push({ key: ++feedSeq, cls: 'done', text: `${name} ✓ done` });
    } else if (t.kind === 'awaiting') {
      out.push({ key: ++feedSeq, cls: 'warn', text: `${name} ? awaiting user` });
    } else if (t.kind === 'rate_limited') {
      out.push({ key: ++feedSeq, cls: 'warn', text: `${name} · rate limited` });
    }
  }
  return out.reverse(); // newest first
}

export function GraphView() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<GraphRenderer | null>(null);
  const prevHostsRef = useRef<Host[]>([]);
  const autoFocusRef = useRef(initialAutoFocusState());

  const hosts = useDashboardStore((s) => s.hosts);
  const selectedWorkerId = useDashboardStore((s) => s.selectedWorkerId);
  const [feed, setFeed] = useState<FeedItem[]>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = new GraphRenderer(canvas, {
      onSelectWorker: (id) => {
        const { selectWorker, closePanel } = useDashboardStore.getState();
        if (id) selectWorker(id);
        else closePanel();
      },
      onSelectTeam: (host, team) =>
        useDashboardStore.getState().selectTeam(host, team),
      onContextMenu: (node, x, y) => {
        const menu = graphNodeMenu(node, x, y);
        if (menu) openContextMenu(menu);
      },
    });
    rendererRef.current = renderer;
    return () => {
      renderer.destroy();
      rendererRef.current = null;
    };
  }, []);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    const nodes = buildGraph(hosts);
    renderer.setNodes(nodes);

    const transitions = diffWorkers(prevHostsRef.current, hosts);
    const events = transitionsToEvents(transitions, nodes);
    for (const b of events.bursts) renderer.burst(b);
    for (const id of events.bumps) renderer.bump(id);
    for (const r of events.ripples) renderer.rippleAtNode(r.id, r.kind);

    const items = transitionsToFeed(transitions, nodes);
    if (items.length) setFeed((prev) => [...items, ...prev].slice(0, FEED_MAX));

    // Camera follow: awaiting-user (oldest wins, holds) > just-completed.
    const af = nextAutoFocus(autoFocusRef.current, hosts, transitions);
    autoFocusRef.current = af.state;
    if (af.focusId) renderer.autoFocusOn(af.focusId);

    prevHostsRef.current = hosts;
  }, [hosts]);

  useEffect(() => {
    rendererRef.current?.setSelected(selectedWorkerId);
  }, [selectedWorkerId]);

  const totalWorkers = hosts.reduce(
    (n, h) => n + h.teams.reduce((m, t) => m + t.workers.length, 0),
    0
  );
  const allStale = hosts.length > 0 && hosts.every(isHostStale);

  return (
    <>
      <ThemeToggle />
      <NotificationToggle />
      <div className={styles.wrap}>
        <canvas ref={canvasRef} className={styles.canvas} aria-label="Worker fleet graph" />
        <div className={styles.hudTop}>
          <span className={styles.brand}>
            DARKARCHON<span className={styles.slash}> //</span> FLEET
          </span>
          <div className={styles.legend}>
            <span><i style={{ background: 'var(--g-busy)' }} />busy</span>
            <span><i style={{ background: 'var(--g-faint)' }} />idle</span>
            <span><i style={{ background: 'var(--g-warn)' }} />awaiting</span>
            <span><i style={{ background: 'var(--g-ok)' }} />done</span>
            <span><i style={{ background: 'var(--g-bad)' }} />dead·limited</span>
            <span><i style={{ background: 'var(--g-accent)' }} />dispatch</span>
            <span><i style={{ background: 'var(--g-orch)' }} />spawned-by</span>
          </div>
        </div>
        {totalWorkers === 0 && !allStale && (
          <div className={styles.empty}>
            <EmptyState variant="no-workers" hostCount={hosts.length} />
          </div>
        )}
        {allStale && (
          <div className={styles.empty}>
            <EmptyState variant="all-stale" hostCount={hosts.length} />
          </div>
        )}
        {feed.length > 0 && (
          <div className={styles.feed} aria-live="polite">
            {feed.map((item, i) => (
              <div
                key={item.key}
                className={`${styles.ev} ${styles[item.cls]} ${i >= 2 ? styles.old : ''}`}
              >
                {item.text}
              </div>
            ))}
          </div>
        )}
        <div className={styles.hint}>
          <kbd>Space</kbd> + drag to pan · scroll to zoom · click a node for
          detail · right-click for actions
        </div>
      </div>
    </>
  );
}
