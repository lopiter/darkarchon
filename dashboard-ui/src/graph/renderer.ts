/**
 * Canvas renderer for the graph view. Owns the camera (Figma-style
 * Space+drag pan, wheel zoom), the rAF loop, and all transient animation
 * state: continuous dispatch streams, one-shot bursts, ripples, badge
 * bumps, busy spinners.
 *
 * Data flows one way — GraphView calls `setNodes` on every store update;
 * the renderer never mutates domain objects.
 *
 * The graph view deliberately keeps its own dark "mission control"
 * palette (the approved mockup identity) instead of the tokens.css
 * families — it is a single-theme canvas world, unaffected by the
 * light/dark toggle. The card view remains fully tokenized.
 */

import type { WorkerState } from '../types/domain';
import { formatPing } from '../utils/formatTime';
import type { FlowBurst } from './flows';
import { shouldDrawLineage, topologyKey, type GraphNode } from './layout';

export interface RendererCallbacks {
  /** Worker card clicked → id; background clicked → null. */
  onSelectWorker: (id: string | null) => void;
  /** Team node clicked → team panel. A team fronted by its orchestrator has
   *  no team node of its own; that node stays a worker click. */
  onSelectTeam: (host: string, team: string) => void;
  /** Right-click on a node. The React side decides what the menu holds. */
  onContextMenu: (node: GraphNode, clientX: number, clientY: number) => void;
}

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace';
const VERBS = ['Whisking', 'Percolating', 'Cogitating', 'Brewing', 'Mustering', 'Noodling'];
const GLYPHS = ['✽', '✻', '✶', '✳'];

const STATE_LABEL: Record<WorkerState, string> = {
  idle: 'idle',
  busy: 'busy',
  compacting: 'compacting',
  'awaiting_user:typed': 'awaiting',
  'awaiting_user:question': 'awaiting',
  'awaiting_user:permission': 'permission',
  rate_limited: 'rate limited',
  dead: 'dead',
  unknown: 'unknown',
};

/** Mockup palette — see GraphView.module.css for the DOM-side twins. */
const PAL = {
  bg: '#070b12',
  card: '#0d1420',
  cardHost: '#101a2b',
  cardEdge: '#1b2637',
  edge: 'rgba(43,56,80,0.9)',
  edgeActive: 'rgba(79,214,255,0.35)',
  grid: 'rgba(138,151,171,0.10)',
  ink: '#e6edf7',
  muted: '#8a97ab',
  faint: '#4a5568',
  accent: '#4fd6ff', // flows, selection
  hover: 'rgba(79,214,255,0.5)',
  busy: '#ffb454', // working
  ok: '#3ddc97', // done / reply
  warn: '#ffcf5c', // awaiting user
  bad: '#ff5c7a', // dead / rate limited
  orch: '#a78bfa', // orchestrator identity
  orchBorder: 'rgba(167,139,250,0.45)',
} as const;

interface Burst extends FlowBurst {
  t0: number;
  count: number;
  stagger: number;
  travel: number;
}

interface Ripple {
  x: number;
  y: number;
  t0: number;
  color: string;
  big: boolean;
}

const BURST_COUNT = 6;
const BURST_STAGGER = 90;
const BURST_TRAVEL = 900;
const STREAM_PARTICLES = 4;
const STREAM_PERIOD = 1600;
const RIPPLE_MS = 550;
const BUMP_MS = 260;
const MIN_SCALE = 0.25;
const MAX_SCALE = 2.5;
const FOCUS_SCALE = 1.5;
const FOCUS_DUR = 420;
/** Auto-focus stays quiet this long after any manual pan/zoom/click. */
const USER_CAM_GRACE_MS = 4000;

type Bezier = [number, number, number, number, number, number, number, number];

function edgePoints(parent: GraphNode, child: GraphNode): Bezier {
  const x0 = parent.x + parent.w;
  const y0 = parent.y;
  const x1 = child.x;
  const y1 = child.y;
  const mx = (x0 + x1) / 2;
  return [x0, y0, mx, y0, mx, y1, x1, y1];
}

function bezierAt(p: Bezier, t: number): [number, number] {
  const u = 1 - t;
  const uu = u * u;
  const tt = t * t;
  return [
    uu * u * p[0] + 3 * uu * t * p[2] + 3 * u * tt * p[4] + tt * t * p[6],
    uu * u * p[1] + 3 * uu * t * p[3] + 3 * u * tt * p[5] + tt * t * p[7],
  ];
}

export class GraphRenderer {
  private cv: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private cb: RendererCallbacks;
  private reduceMotion: boolean;

  private nodes: GraphNode[] = [];
  private byId = new Map<string, GraphNode>();
  /** Worker display name → node, for resolving dispatch senders
   * (incoming_dispatches[].label carries the sender's name). */
  private byName = new Map<string, GraphNode>();
  private topoKey = '';

  private cam = { x: 0, y: 0, s: 1 };
  private camAnim: {
    fromX: number;
    fromY: number;
    fromS: number;
    toX: number;
    toY: number;
    toS: number;
    t0: number;
    dur: number;
  } | null = null;
  private dpr = 1;
  private vw = 0;
  private vh = 0;

  private bursts: Burst[] = [];
  private ripples: Ripple[] = [];
  private bumpT0 = new Map<string, number>();
  private busySince = new Map<string, number>();
  private prevState = new Map<string, WorkerState>();
  private verbSeed = new Map<string, number>();

  private selectedId: string | null = null;
  private hoverId: string | null = null;
  private lastUserCamAt = -Infinity;
  private spaceHeld = false;
  private panning = false;
  private panStart: { mx: number; my: number; cx: number; cy: number } | null = null;

  private raf = 0;
  private disposers: Array<() => void> = [];

  constructor(canvas: HTMLCanvasElement, cb: RendererCallbacks) {
    this.cv = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('graph view needs a 2d canvas context');
    this.ctx = ctx;
    this.cb = cb;
    this.reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

    this.bindEvents();
    this.resize();
    this.raf = requestAnimationFrame(this.frame);
  }

  /* ── public API ─────────────────────────────────────────────── */

  setNodes(nodes: GraphNode[]): void {
    this.nodes = nodes;
    this.byId = new Map(nodes.map((n) => [n.id, n]));
    this.byName = new Map();
    for (const n of nodes) {
      if (n.worker && !this.byName.has(n.label)) this.byName.set(n.label, n);
    }

    const now = performance.now();
    for (const n of nodes) {
      if (!n.worker) continue;
      const prev = this.prevState.get(n.id);
      const cur = n.worker.state;
      if (cur === 'busy' || cur === 'compacting') {
        if (prev !== 'busy' && prev !== 'compacting') this.busySince.set(n.id, now);
      } else {
        this.busySince.delete(n.id);
      }
      this.prevState.set(n.id, cur);
      if (!this.verbSeed.has(n.id)) {
        this.verbSeed.set(n.id, Math.floor(Math.random() * VERBS.length));
      }
    }

    const key = topologyKey(nodes);
    if (key !== this.topoKey) {
      this.topoKey = key;
      this.fit();
    }
  }

  setSelected(id: string | null): void {
    this.selectedId = id;
  }

  /** State-driven camera follow (awaiting > done). Skipped while the user
   * is steering the camera themselves — never fight a human hand. */
  autoFocusOn(id: string): void {
    if (performance.now() - this.lastUserCamAt < USER_CAM_GRACE_MS) return;
    const n = this.byId.get(id);
    if (n) this.focusOn(n);
  }

  burst(f: FlowBurst): void {
    this.bursts.push({
      ...f,
      t0: performance.now(),
      count: this.reduceMotion ? 0 : f.kind === 'question' ? 3 : BURST_COUNT,
      stagger: BURST_STAGGER,
      travel: BURST_TRAVEL,
    });
  }

  bump(nodeId: string): void {
    this.bumpT0.set(nodeId, performance.now());
  }

  rippleAtNode(nodeId: string, kind: 'amber' | 'red'): void {
    const n = this.byId.get(nodeId);
    if (!n) return;
    this.ripples.push({
      x: n.x + n.w / 2,
      y: n.y,
      t0: performance.now(),
      color: kind === 'amber' ? PAL.warn : PAL.bad,
      big: true,
    });
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    for (const d of this.disposers) d();
    this.disposers = [];
  }

  /* ── events ─────────────────────────────────────────────────── */

  private bindEvents(): void {
    const on = <K extends keyof WindowEventMap>(
      target: Window | HTMLElement,
      type: string,
      fn: (e: WindowEventMap[K]) => void,
      opts?: AddEventListenerOptions
    ) => {
      target.addEventListener(type, fn as EventListener, opts);
      this.disposers.push(() => target.removeEventListener(type, fn as EventListener, opts));
    };

    on<'keydown'>(window, 'keydown', (e) => {
      if (e.code === 'Space' && !e.repeat && document.activeElement === document.body) {
        this.spaceHeld = true;
        e.preventDefault();
        this.syncCursor();
      }
    });
    on<'keyup'>(window, 'keyup', (e) => {
      if (e.code === 'Space') {
        this.spaceHeld = false;
        this.panning = false;
        this.syncCursor();
      }
    });
    on<'blur'>(window, 'blur', () => {
      this.spaceHeld = false;
      this.panning = false;
      this.syncCursor();
    });
    on<'resize'>(window, 'resize', () => this.resize());

    on<'pointerdown'>(this.cv, 'pointerdown', (e) => {
      try {
        this.cv.setPointerCapture(e.pointerId);
      } catch {
        /* synthetic pointers in tests have no active pointer id */
      }
      if (this.spaceHeld || e.button === 1) {
        this.panning = true;
        this.panStart = { mx: e.clientX, my: e.clientY, cx: this.cam.x, cy: this.cam.y };
        this.camAnim = null;
        this.lastUserCamAt = performance.now();
        e.preventDefault();
        this.syncCursor();
      }
    });
    on<'pointermove'>(this.cv, 'pointermove', (e) => {
      if (this.panning && this.panStart) {
        this.cam.x = this.panStart.cx + (e.clientX - this.panStart.mx);
        this.cam.y = this.panStart.cy + (e.clientY - this.panStart.my);
      } else {
        const hit = this.hitTest(e.clientX, e.clientY);
        this.hoverId =
          hit && (hit.kind === 'worker' || hit.kind === 'team') ? hit.id : null;
        this.syncCursor();
      }
    });
    on<'pointerup'>(this.cv, 'pointerup', (e) => {
      if (this.panning) {
        this.panning = false;
        this.syncCursor();
        return;
      }
      if (e.button !== 0) return;
      const hit = this.hitTest(e.clientX, e.clientY);
      if (hit && (hit.kind === 'worker' || hit.kind === 'team')) {
        this.ripples.push({
          x: hit.x + hit.w / 2,
          y: hit.y,
          t0: performance.now(),
          color: PAL.accent,
          big: true,
        });
        this.lastUserCamAt = performance.now();
        this.focusOn(hit);
        if (hit.kind === 'team' && hit.team) {
          this.cb.onSelectTeam(hit.team.host, hit.team.name);
        } else {
          this.cb.onSelectWorker(hit.id);
        }
      } else {
        const [wx, wy] = this.toWorld(e.clientX, e.clientY);
        this.ripples.push({ x: wx, y: wy, t0: performance.now(), color: PAL.faint, big: false });
        this.cb.onSelectWorker(null);
      }
    });
    on<'wheel'>(this.cv, 'wheel', (e) => {
      e.preventDefault();
      this.camAnim = null;
      this.lastUserCamAt = performance.now();
      const f = Math.exp(-e.deltaY * 0.0012);
      const s2 = Math.min(MAX_SCALE, Math.max(MIN_SCALE, this.cam.s * f));
      this.cam.x = e.clientX - (e.clientX - this.cam.x) * (s2 / this.cam.s);
      this.cam.y = e.clientY - (e.clientY - this.cam.y) * (s2 / this.cam.s);
      this.cam.s = s2;
    }, { passive: false });
    // Bound to `window` rather than the canvas: when a node is selected, the
    // DetailPanel's full-viewport backdrop (z-index above the canvas, needed
    // to catch outside-clicks that close the panel) sits over the canvas and
    // becomes the event's target, so a canvas-only listener would never see
    // right-clicks while a node is active. A window listener still fires
    // regardless of which overlay the pointer actually hit; the bounds check
    // below keeps clicks outside the canvas rect (HUD, panel) from opening it.
    on<'contextmenu'>(window, 'contextmenu', (e) => {
      const rect = this.cv.getBoundingClientRect();
      if (
        e.clientX < rect.left ||
        e.clientX > rect.right ||
        e.clientY < rect.top ||
        e.clientY > rect.bottom
      ) {
        return;
      }
      const hit = this.hitTest(e.clientX, e.clientY);
      // Only swallow the browser menu when there is something under the
      // cursor to offer instead — a right-click on empty canvas stays the
      // browser's.
      if (!hit) return;
      e.preventDefault();
      this.cb.onContextMenu(hit, e.clientX, e.clientY);
    });
  }

  private syncCursor(): void {
    this.cv.style.cursor = this.panning
      ? 'grabbing'
      : this.spaceHeld
        ? 'grab'
        : this.hoverId
          ? 'pointer'
          : 'default';
  }

  /* ── camera ─────────────────────────────────────────────────── */

  private resize(): void {
    this.dpr = Math.min(devicePixelRatio || 1, 2);
    this.vw = this.cv.clientWidth;
    this.vh = this.cv.clientHeight;
    this.cv.width = Math.max(1, this.vw * this.dpr);
    this.cv.height = Math.max(1, this.vh * this.dpr);
  }

  private fit(): void {
    if (!this.nodes.length || !this.vw) return;
    this.camAnim = null;
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const n of this.nodes) {
      x0 = Math.min(x0, n.x);
      y0 = Math.min(y0, n.y - n.h / 2);
      x1 = Math.max(x1, n.x + n.w);
      y1 = Math.max(y1, n.y + n.h / 2);
    }
    const pad = 90;
    this.cam.s = Math.min(
      (this.vw - pad * 2) / Math.max(1, x1 - x0),
      (this.vh - pad * 2) / Math.max(1, y1 - y0),
      1.15
    );
    this.cam.s = Math.max(MIN_SCALE, this.cam.s);
    this.cam.x = (this.vw - (x1 - x0) * this.cam.s) / 2 - x0 * this.cam.s;
    this.cam.y = (this.vh - (y1 - y0) * this.cam.s) / 2 - y0 * this.cam.s;
  }

  /** Smoothly recenters + zooms the camera on a clicked node. Never zooms
   * out — if the user is already closer than FOCUS_SCALE, that level holds. */
  private focusOn(n: GraphNode): void {
    const targetScale = Math.min(MAX_SCALE, Math.max(this.cam.s, FOCUS_SCALE));
    const cx = n.x + n.w / 2;
    const cy = n.y;
    const toX = this.vw / 2 - cx * targetScale;
    const toY = this.vh / 2 - cy * targetScale;
    if (this.reduceMotion) {
      this.cam.x = toX;
      this.cam.y = toY;
      this.cam.s = targetScale;
      this.camAnim = null;
      return;
    }
    this.camAnim = {
      fromX: this.cam.x,
      fromY: this.cam.y,
      fromS: this.cam.s,
      toX,
      toY,
      toS: targetScale,
      t0: performance.now(),
      dur: FOCUS_DUR,
    };
  }

  private toWorld(sx: number, sy: number): [number, number] {
    return [(sx - this.cam.x) / this.cam.s, (sy - this.cam.y) / this.cam.s];
  }

  private hitTest(sx: number, sy: number): GraphNode | null {
    const [wx, wy] = this.toWorld(sx, sy);
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i]!;
      if (wx >= n.x && wx <= n.x + n.w && wy >= n.y - n.h / 2 && wy <= n.y + n.h / 2) {
        return n;
      }
    }
    return null;
  }

  /* ── drawing ────────────────────────────────────────────────── */

  private rr(x: number, y: number, w: number, h: number, r: number): void {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  private stateColor(s: WorkerState): string {
    if (s === 'busy' || s === 'compacting') return PAL.busy;
    if (s.startsWith('awaiting_user:')) return PAL.warn;
    if (s === 'rate_limited' || s === 'dead' || s === 'unknown') return PAL.bad;
    return PAL.faint;
  }

  private frame = (): void => {
    const t = performance.now();
    const c = this.ctx;

    if (this.camAnim) {
      const a = this.camAnim;
      const p = Math.min(1, (t - a.t0) / a.dur);
      const eased = 1 - (1 - p) ** 3; // ease-out cubic
      this.cam.x = a.fromX + (a.toX - a.fromX) * eased;
      this.cam.y = a.fromY + (a.toY - a.fromY) * eased;
      this.cam.s = a.fromS + (a.toS - a.fromS) * eased;
      if (p >= 1) this.camAnim = null;
    }

    if (this.cv.clientWidth !== this.vw || this.cv.clientHeight !== this.vh) this.resize();

    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = PAL.bg;
    c.fillRect(0, 0, this.vw, this.vh);
    this.drawGrid();

    c.setTransform(
      this.dpr * this.cam.s,
      0,
      0,
      this.dpr * this.cam.s,
      this.dpr * this.cam.x,
      this.dpr * this.cam.y
    );

    // static tree edges
    for (const n of this.nodes) {
      if (!n.parentId) continue;
      const parent = this.byId.get(n.parentId);
      if (!parent) continue;
      const p = edgePoints(parent, n);
      c.beginPath();
      c.moveTo(p[0], p[1]);
      c.bezierCurveTo(p[2], p[3], p[4], p[5], p[6], p[7]);
      c.strokeStyle = PAL.edge;
      c.lineWidth = 1.2;
      c.stroke();
    }

    // lineage links — persistent "spawned by" relations (worker.spawnedBy)
    // whose spawner is NOT already the tree parent, e.g. hermes-spawned
    // orchestrators living in their own team. Dashed violet, no animation.
    c.setLineDash([5, 5]);
    for (const n of this.nodes) {
      const by = n.worker?.spawnedBy;
      if (!by) continue;
      const src = this.byName.get(by);
      if (!shouldDrawLineage(n, src)) continue;
      const p = edgePoints(src!, n);
      c.beginPath();
      c.moveTo(p[0], p[1]);
      c.bezierCurveTo(p[2], p[3], p[4], p[5], p[6], p[7]);
      c.strokeStyle = PAL.orchBorder;
      c.lineWidth = 1.2;
      c.stroke();
    }
    c.setLineDash([]);

    // dispatch streams — drawn from the actual sender when the dispatch
    // label resolves to a node (e.g. hermes driving a worker in another
    // team), otherwise from the tree parent.
    for (const n of this.nodes) {
      const w = n.worker;
      if (!w?.dispatchIn) continue;
      const sources = new Set<GraphNode>();
      for (const d of w.incomingDispatches) {
        const sender = this.byName.get(d.label);
        if (sender && sender !== n) sources.add(sender);
      }
      if (!sources.size && n.parentId) {
        const parent = this.byId.get(n.parentId);
        if (parent) sources.add(parent);
      }
      for (const src of sources) {
        const p = edgePoints(src, n);
        c.beginPath();
        c.moveTo(p[0], p[1]);
        c.bezierCurveTo(p[2], p[3], p[4], p[5], p[6], p[7]);
        c.strokeStyle = PAL.edgeActive;
        c.lineWidth = 1.6;
        c.stroke();
        if (!this.reduceMotion) this.drawStream(p, t);
      }
    }

    this.drawBursts(t);
    for (const n of this.nodes) this.drawNode(n, t);
    this.drawRipples(t);

    this.raf = requestAnimationFrame(this.frame);
  };

  private drawGrid(): void {
    const c = this.ctx;
    const step = 44 * this.cam.s;
    if (step < 14) return;
    c.fillStyle = PAL.grid;
    const ox = ((this.cam.x % step) + step) % step;
    const oy = ((this.cam.y % step) + step) % step;
    for (let x = ox; x < this.vw; x += step) {
      for (let y = oy; y < this.vh; y += step) {
        c.fillRect(x, y, 1.2, 1.2);
      }
    }
  }

  private drawComet(p: Bezier, progress: number, color: string, backwards: boolean): void {
    const c = this.ctx;
    const [px, py] = bezierAt(p, progress);
    c.shadowColor = color;
    c.shadowBlur = 8;
    c.fillStyle = color;
    c.beginPath();
    c.arc(px, py, 2.6, 0, 7);
    c.fill();
    c.shadowBlur = 0;
    for (let s = 1; s <= 3; s++) {
      const tp = progress + (backwards ? 1 : -1) * s * 0.03;
      if (tp < 0 || tp > 1) continue;
      const [tx, ty] = bezierAt(p, tp);
      c.globalAlpha = 0.3 / s;
      c.beginPath();
      c.arc(tx, ty, 2.2, 0, 7);
      c.fill();
      c.globalAlpha = 1;
    }
  }

  private drawStream(p: Bezier, t: number): void {
    for (let k = 0; k < STREAM_PARTICLES; k++) {
      const progress = (t / STREAM_PERIOD + k / STREAM_PARTICLES) % 1;
      this.drawComet(p, progress, PAL.accent, false);
    }
  }

  private drawBursts(t: number): void {
    for (let i = this.bursts.length - 1; i >= 0; i--) {
      const b = this.bursts[i]!;
      const from = this.byId.get(b.fromId);
      const to = this.byId.get(b.toId);
      if (!from || !to || b.count === 0) {
        this.bursts.splice(i, 1);
        continue;
      }
      // bursts run child → parent, so the edge is parent→child reversed
      const p = edgePoints(to, from);
      const color = b.kind === 'question' ? PAL.warn : PAL.ok;
      let alive = false;
      for (let k = 0; k < b.count; k++) {
        const pr = (t - b.t0 - k * b.stagger) / b.travel;
        if (pr < 0 || pr > 1) continue;
        alive = true;
        this.drawComet(p, 1 - pr, color, true);
      }
      if (!alive && t - b.t0 > b.count * b.stagger + b.travel) this.bursts.splice(i, 1);
    }
  }

  private drawNode(n: GraphNode, t: number): void {
    const c = this.ctx;
    const x = n.x;
    const y = n.y - n.h / 2;
    const w = n.w;
    const h = n.h;
    const state: WorkerState = n.worker?.state ?? 'idle';
    const isDead = state === 'dead' || state === 'unknown';
    const isBusy = state === 'busy' || state === 'compacting';
    const awaiting = state.startsWith('awaiting_user:');
    // Unreviewed result (unseenDone). While idle the whole node reads green;
    // if the worker is already busy again only the ✓ glyph below remains.
    const done = state === 'idle' && n.worker?.unseenDone === true;
    const sc = done ? PAL.ok : this.stateColor(state);
    const stripe =
      n.kind !== 'worker' ? PAL.accent : n.worker?.isOrchestrator ? PAL.orch : sc;

    c.globalAlpha = isDead ? 0.45 : 1;

    // glow — done gets a steady (non-pulsing) glow: results accumulate
    // quietly, they don't clamor like awaiting/busy.
    let glow = 0;
    if (!this.reduceMotion) {
      if (isBusy) glow = 12 + 5 * Math.sin(t / 260 + n.y);
      else if (awaiting) glow = 10 + 7 * Math.sin(t / 420);
      else if (state === 'rate_limited') glow = 9 + 5 * Math.sin(t / 700);
      else if (done) glow = 7;
    } else if (isBusy || awaiting || state === 'rate_limited' || done) {
      glow = done ? 7 : 9;
    }
    if (this.selectedId === n.id) glow = Math.max(glow, 14);

    c.shadowColor = this.selectedId === n.id ? PAL.accent : sc;
    c.shadowBlur = glow;
    c.fillStyle = n.kind === 'host' ? PAL.cardHost : PAL.card;
    this.rr(x, y, w, h, 8);
    c.fill();
    c.shadowBlur = 0;

    // border
    c.strokeStyle =
      this.selectedId === n.id
        ? PAL.accent
        : this.hoverId === n.id
          ? PAL.hover
          : n.kind !== 'worker' || n.worker?.isOrchestrator
            ? PAL.orchBorder
            : PAL.cardEdge;
    c.lineWidth = this.selectedId === n.id ? 1.8 : 1.2;
    this.rr(x, y, w, h, 8);
    c.stroke();

    // left accent stripe
    c.fillStyle = stripe;
    this.rr(x, y, 4, h, 2);
    c.fill();

    // status dot (+ spinner while busy)
    const dx = x + 20;
    const dy = n.y;
    c.fillStyle = sc;
    c.beginPath();
    c.arc(dx, dy, 4, 0, 7);
    c.fill();
    if (isBusy) {
      c.strokeStyle = PAL.busy;
      c.lineWidth = 1.6;
      const a = this.reduceMotion ? 0.8 : t / 300;
      c.beginPath();
      c.arc(dx, dy, 8.5, a, a + 4.2);
      c.stroke();
    }
    if (awaiting) {
      c.fillStyle = PAL.warn;
      c.font = `700 12px ${MONO}`;
      c.fillText('?', dx + 7, dy - 6);
    } else if (!isDead && n.worker?.unseenDone) {
      // Same slot as the awaiting '?' — the amber question outranks it when
      // both apply, since a blocked worker needs the human first.
      c.fillStyle = PAL.ok;
      c.font = `700 12px ${MONO}`;
      c.fillText('✓', dx + 7, dy - 6);
    }

    // label
    c.fillStyle = PAL.ink;
    c.font = `${n.kind !== 'worker' || n.worker?.isOrchestrator ? 700 : 600} 13px ${MONO}`;
    c.fillText(this.clip(n.label, 24), x + 36, dy - 3);

    // subtitle
    c.font = `10px ${MONO}`;
    if (n.kind === 'worker' && isBusy) {
      const since = this.busySince.get(n.id);
      const secs = since === undefined ? null : Math.floor((t - since) / 1000);
      const detail = n.worker?.detail;
      const seed = this.verbSeed.get(n.id) ?? 0;
      const verb = VERBS[(seed + Math.floor(t / 2600)) % VERBS.length];
      const glyph = this.reduceMotion ? '✽' : GLYPHS[Math.floor(t / 240) % GLYPHS.length];
      const text = detail
        ? `${glyph} ${detail}`
        : `${glyph} ${verb}…${secs === null ? '' : ` (${secs}s)`}`;
      c.fillStyle = PAL.busy;
      c.fillText(this.clip(text, 30), x + 36, dy + 12);
    } else {
      c.fillStyle = done ? PAL.ok : PAL.muted;
      const fin = n.worker?.finishedAtMs;
      const stateText = done
        ? `done${fin ? ` · ${formatPing(Date.now() - fin)}` : ''}`
        : STATE_LABEL[state];
      const sub = n.kind === 'worker' ? `${n.sub} · ${stateText}` : n.sub;
      c.fillText(this.clip(sub, 30), x + 36, dy + 12);
    }

    // orchestrator tag + mailbox badge
    if (n.worker?.isOrchestrator) {
      c.fillStyle = PAL.orch;
      c.font = `700 9px ${MONO}`;
      c.fillText('ORCH', x + w - 38, y + 14);
    }
    const mailbox = n.worker?.mailboxPending ?? 0;
    if (mailbox > 0) {
      let scale = 1;
      const bumpT = this.bumpT0.get(n.id);
      if (!this.reduceMotion && bumpT !== undefined) {
        const dt = (t - bumpT) / BUMP_MS;
        if (dt < 1) scale = 1 + 0.5 * Math.sin(dt * Math.PI);
        else this.bumpT0.delete(n.id);
      }
      const bx = x + w - 10;
      const by = y + 2;
      c.fillStyle = PAL.busy;
      c.beginPath();
      c.arc(bx, by, 8 * scale, 0, 7);
      c.fill();
      c.fillStyle = PAL.bg;
      c.font = `700 9px ${MONO}`;
      c.textAlign = 'center';
      c.fillText(String(mailbox), bx, by + 3);
      c.textAlign = 'left';
    }

    c.globalAlpha = 1;
  }

  private drawRipples(t: number): void {
    const c = this.ctx;
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const r = this.ripples[i]!;
      const pr = (t - r.t0) / RIPPLE_MS;
      if (pr > 1 || this.reduceMotion) {
        this.ripples.splice(i, 1);
        continue;
      }
      c.globalAlpha = (1 - pr) * 0.8;
      c.strokeStyle = r.color;
      c.lineWidth = 2 - pr;
      c.beginPath();
      c.arc(r.x, r.y, (r.big ? 70 : 40) * pr + 6, 0, 7);
      c.stroke();
      c.globalAlpha = 1;
    }
  }

  private clip(text: string, max: number): string {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }
}
