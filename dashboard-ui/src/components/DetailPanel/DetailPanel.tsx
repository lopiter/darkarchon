/**
 * DESIGN.md Section 6 — Detail sidepanel.
 *
 * Sliding container + 6 sub-sections (header / awaiting zone / tmux target /
 * recent output / in-flight / mailbox / activity). Read-only by design — no
 * action buttons except the clipboard-copy on the tmux target.
 */

import { useEffect, useMemo, useState } from 'react';
import { useClipboard } from '../../hooks/useClipboard';
import { useEscapeKey } from '../../hooks/useEscapeKey';
import { useDashboardStore } from '../../store/dashboard';
import type { Worker } from '../../types/domain';
import type { RawDispatchEntry, RawTask } from '../../types/raw';
import { sortWorkersForTeam } from '../../utils/sortWorkers';
import styles from './DetailPanel.module.css';

interface Selected {
  worker: Worker;
  host: string;
  team: string;
}

function useSelectedWorker(): Selected | null {
  const id = useDashboardStore((s) => s.selectedWorkerId);
  const hosts = useDashboardStore((s) => s.hosts);
  return useMemo(() => {
    if (!id) return null;
    for (const h of hosts) {
      for (const t of h.teams) {
        const w = t.workers.find((w) => w.id === id);
        if (w) return { worker: w, host: h.id, team: t.name };
      }
    }
    return null;
  }, [id, hosts]);
}

/**
 * Arrow-key navigation while the panel is open. Up/Left → prev worker,
 * Down/Right → next worker, wrapping at both ends. Order follows the
 * on-screen sort (sortWorkersForTeam) so movement matches what the user sees.
 */
function useArrowKeyNav(active: boolean): void {
  const selectWorker = useDashboardStore((s) => s.selectWorker);
  const hosts = useDashboardStore((s) => s.hosts);
  const selectedId = useDashboardStore((s) => s.selectedWorkerId);

  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      if (
        e.key !== 'ArrowDown' &&
        e.key !== 'ArrowUp' &&
        e.key !== 'ArrowLeft' &&
        e.key !== 'ArrowRight'
      ) {
        return;
      }
      e.preventDefault();
      const ids: string[] = [];
      for (const h of hosts) {
        for (const t of h.teams) {
          for (const w of sortWorkersForTeam(t.workers, true)) {
            ids.push(w.id);
          }
        }
      }
      if (ids.length === 0) return;
      const dir = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
      const cur = selectedId ? ids.indexOf(selectedId) : -1;
      const next =
        cur === -1
          ? dir > 0
            ? 0
            : ids.length - 1
          : (cur + dir + ids.length) % ids.length;
      selectWorker(ids[next]!);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [active, hosts, selectedId, selectWorker]);
}

const isAwaiting = (s: Worker['state']) =>
  s === 'awaiting_user:typed' || s === 'awaiting_user:question';

export function DetailPanel() {
  const selected = useSelectedWorker();
  const closePanel = useDashboardStore((s) => s.closePanel);
  const open = selected !== null;

  useEscapeKey(open, closePanel);
  useArrowKeyNav(open);

  return (
    <>
      {open && (
        <div className={styles.backdrop} onClick={closePanel} aria-hidden="true" />
      )}
      <aside
        className={`${styles.panel} ${open ? styles.open : ''}`}
        aria-hidden={!open}
        aria-label="Worker detail panel"
      >
        {selected && <PanelContent selected={selected} onClose={closePanel} />}
      </aside>
    </>
  );
}

function PanelContent({
  selected,
  onClose,
}: {
  selected: Selected;
  onClose: () => void;
}) {
  const { worker, host, team } = selected;
  return (
    <>
      <PanelHeader worker={worker} host={host} team={team} onClose={onClose} />
      {isAwaiting(worker.state) && worker.detail && (
        <AwaitingZone detail={worker.detail} />
      )}
      <TmuxTargetSection target={worker.tmuxTarget} />
      {worker.recentOutput && worker.recentOutput.length > 0 && (
        <RecentOutputSection lines={worker.recentOutput} />
      )}
      {(worker.incomingDispatches.length > 0 ||
        worker.outgoingDispatches.length > 0) && (
        <InFlightSection
          incoming={worker.incomingDispatches}
          outgoing={worker.outgoingDispatches}
        />
      )}
      {worker.mailboxPending > 0 && (
        <MailboxSection
          count={worker.mailboxPending}
          senders={worker.mailboxSenders}
        />
      )}
      <ActivitySection tasks={worker.recentTasks} />
    </>
  );
}

function PanelHeader({
  worker,
  host,
  team,
  onClose,
}: {
  worker: Worker;
  host: string;
  team: string;
  onClose: () => void;
}) {
  const pillClass =
    isAwaiting(worker.state)
      ? `${styles.statePill} ${styles.amber}`
      : worker.state === 'rate_limited'
        ? `${styles.statePill} ${styles.red}`
        : styles.statePill;

  const stateLabel =
    worker.state === 'awaiting_user:typed' || worker.state === 'awaiting_user:question'
      ? 'awaiting'
      : worker.state === 'rate_limited'
        ? 'rate limited'
        : worker.state;

  const meta = [host, worker.role || team].filter(Boolean).join(' · ');

  return (
    <header className={styles.header}>
      <div className={styles.headerLeft}>
        <span className={styles.headerName}>
          {worker.name}
          <span className={pillClass}>{stateLabel}</span>
        </span>
        <span className={styles.headerMeta}>{meta}</span>
      </div>
      <button
        type="button"
        className={styles.closeButton}
        onClick={onClose}
        aria-label="Close panel"
      >
        ×
      </button>
    </header>
  );
}

function AwaitingZone({ detail }: { detail: string }) {
  return (
    <section className={`${styles.section} ${styles.awaiting}`}>
      <div className={styles.sectionLabel}>Awaiting</div>
      <div className={styles.awaitingCmd}>{detail}</div>
    </section>
  );
}

function TmuxTargetSection({ target }: { target: string }) {
  const { copy, copied } = useClipboard();
  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>Tmux Target</div>
      <button
        type="button"
        className={styles.tmuxButton}
        onClick={() => copy(target)}
      >
        <span className={styles.tmuxTargetText}>{target}</span>
        <span
          className={`${styles.tmuxCopyLabel} ${copied ? styles.copied : ''}`}
        >
          {copied ? '✓ copied' : '📋 copy'}
        </span>
      </button>
      <span className={styles.tmuxHint}>
        본인 터미널에서 <code>tmux attach -t [paste]</code>
      </span>
    </section>
  );
}

function RecentOutputSection({ lines }: { lines: string[] }) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>
        Recent Output<span>last {lines.length} lines</span>
      </div>
      <div className={styles.terminal}>{lines.join('\n')}</div>
    </section>
  );
}

function InFlightSection({
  incoming,
  outgoing,
}: {
  incoming: RawDispatchEntry[];
  outgoing: RawDispatchEntry[];
}) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>In-Flight</div>
      {incoming.map((e, i) => (
        <div key={`in-${i}`} className={styles.dispatchRow}>
          <span className={styles.dispatchArrow}>←</span>
          {e.label}
          <span className={styles.tmuxHint}>{relative(e.started_at)}</span>
        </div>
      ))}
      {outgoing.map((e, i) => (
        <div key={`out-${i}`} className={styles.dispatchRow}>
          <span className={styles.dispatchArrow}>→</span>
          {e.label}
          <span className={styles.tmuxHint}>{relative(e.started_at)}</span>
        </div>
      ))}
    </section>
  );
}

function MailboxSection({
  count,
  senders,
}: {
  count: number;
  senders: string[];
}) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>
        Mailbox<span>{count} pending</span>
      </div>
      {senders.length > 0 && (
        <div className={styles.mailboxRow}>from: {senders.join(', ')}</div>
      )}
    </section>
  );
}

function ActivitySection({ tasks }: { tasks: RawTask[] }) {
  const [expanded, setExpanded] = useState(false);
  const summary = tasks.length > 0 ? `${tasks.length} recent tasks` : 'no recent activity';
  return (
    <section className={styles.section}>
      <div
        className={`${styles.sectionLabel} ${styles.activityHeader}`}
        onClick={() => setExpanded((e) => !e)}
      >
        <span>Activity</span>
        <span>
          {summary} {tasks.length > 0 && (expanded ? '▴' : '▾')}
        </span>
      </div>
      {expanded && tasks.length > 0 && (
        <div className={styles.activityList}>
          {tasks.slice(0, 10).map((t) => (
            <div key={t.id} className={styles.activityRow}>
              [{t.status}] {t.id} · {relative(t.dispatched_at)}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function relative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diffSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}
