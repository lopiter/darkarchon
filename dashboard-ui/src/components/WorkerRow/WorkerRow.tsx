/**
 * v2 row layout for a single worker. DESIGN.md Section 3.4 (post-patch).
 *
 * The row is a 28px-tall CSS grid with fixed column widths so columns
 * line up across hosts and teams. The left 4px vertical bar carries
 * the single semantic color (awaiting / rate / orchestrator).
 *
 * Pulse (amber/red) animates both the row background and the left bar
 * glow simultaneously, then fades back to the steady-state tint that the
 * row already shows for that state. Mailbox bump scales the inline
 * badge. Dispatch arrows fade in/out as flags toggle.
 *
 * The `key` trick (pulseEntry.key) restarts the keyframes if the same
 * worker pulses twice in a row.
 */

import type { KeyboardEvent } from 'react';
import { AgentLogo } from '../AgentLogo/AgentLogo';
import { onWorkerContextMenu } from '../ContextMenu/menus';
import { useDashboardStore } from '../../store/dashboard';
import type { Worker, WorkerState } from '../../types/domain';
import { formatPing } from '../../utils/formatTime';
import styles from './WorkerRow.module.css';

interface Props {
  worker: Worker;
  hostStale?: boolean;
  /** When true, plays the exit keyframes instead of staying static.
   * TeamSection sets this for ~300ms after a worker disappears from
   * the sorted list, then unmounts it. */
  exiting?: boolean;
}

// Every awaiting_user:* variant means a human has to act, so match on the
// prefix rather than listing them — a new variant must not quietly stop
// counting as "awaiting" here while the sort and notification paths include it.
const isAwaiting = (s: WorkerState) => s.startsWith('awaiting_user:');

const STATE_DOT: Record<WorkerState, string> = {
  idle: styles.dotIdle ?? '',
  busy: styles.dotBusy ?? '',
  compacting: styles.dotBusy ?? '',
  'awaiting_user:typed': styles.dotAwaiting ?? '',
  'awaiting_user:question': styles.dotAwaiting ?? '',
  'awaiting_user:permission': styles.dotAwaiting ?? '',
  rate_limited: styles.dotRate ?? '',
  dead: styles.dotDead ?? '',
  unknown: styles.dotDead ?? '',
};

const STATE_LABEL: Record<WorkerState, string> = {
  idle: 'idle',
  busy: 'busy',
  compacting: 'compacting',
  'awaiting_user:typed': 'awaiting',
  'awaiting_user:question': 'awaiting',
  // Labelled apart from the others: this one is cleared by approving a prompt
  // in the pane, not by answering a question the worker asked.
  'awaiting_user:permission': 'permission',
  rate_limited: 'rate limited',
  dead: 'dead',
  unknown: 'unknown',
};

export function WorkerRow({ worker, hostStale = false, exiting = false }: Props) {
  const pulse = useDashboardStore((s) => s.pulseUntil.get(worker.id));
  const isNew = useDashboardStore((s) => s.newUntil.has(worker.id));
  const selectWorker = useDashboardStore((s) => s.selectWorker);

  const awaiting = isAwaiting(worker.state);
  const rate = worker.state === 'rate_limited';
  const isDead = worker.state === 'dead' || worker.state === 'unknown';
  // Unreviewed result. While the worker sits idle the whole row goes green
  // ("done"); if it's already busy on the next task, only a ✓ badge remains.
  const done = worker.state === 'idle' && worker.unseenDone;
  const doneBadge = worker.unseenDone && !done && !isDead;

  const barClass = awaiting
    ? styles.barAmber
    : rate
      ? styles.barRed
      : done
        ? styles.barGreen
        : worker.isOrchestrator
          ? styles.barOrch
          : '';

  const rowTintClass = awaiting
    ? styles.rowAwaiting
    : rate
      ? styles.rowRate
      : done
        ? styles.rowDone
        : '';

  const deadClass = isDead && !hostStale ? styles.rowDead : '';

  const pulseRowClass =
    pulse?.color === 'amber'
      ? styles.pulseAmberRow
      : pulse?.color === 'red'
        ? styles.pulseRedRow
        : '';
  const pulseBarClass =
    pulse?.color === 'amber'
      ? styles.pulseAmberBar
      : pulse?.color === 'red'
        ? styles.pulseRedBar
        : '';
  const mailboxBumpClass =
    pulse?.color === 'scale' ? styles.mailboxBump : '';

  const detailClass = awaiting
    ? `${styles.detail} ${styles.detailAwaiting}`
    : rate
      ? `${styles.detail} ${styles.detailRate}`
      : styles.detail;

  const onClick = () => selectWorker(worker.id);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      selectWorker(worker.id);
    }
  };

  const rowKey = pulseRowClass ? `pulse-${pulse!.key}` : 'idle';

  return (
    <div
      key={rowKey}
      className={[
        styles.row,
        rowTintClass,
        deadClass,
        pulseRowClass,
        exiting ? styles.rowExit : '',
      ]
        .filter(Boolean)
        .join(' ')}
      onClick={onClick}
      onContextMenu={onWorkerContextMenu(worker)}
      onKeyDown={onKeyDown}
      role="button"
      tabIndex={exiting ? -1 : 0}
      data-card-wrapper="1"
      aria-hidden={exiting || undefined}
    >
      <div className={[styles.bar, barClass, pulseBarClass].filter(Boolean).join(' ')} />
      {isNew && <div className={styles.newDot} aria-label="new awaiting notification" />}

      <div className={styles.name}>
        <AgentLogo process={worker.process} size={14} />
        <span>{worker.name}</span>
        {worker.isOrchestrator && <span className={styles.orchTag}>ORCH</span>}
      </div>

      <div className={styles.target}>{worker.tmuxTarget}</div>

      <div>
        {doneBadge && (
          <span
            className={styles.doneBadge}
            aria-label="finished a task you haven't reviewed"
          >
            ✓
          </span>
        )}
        {worker.mailboxPending > 0 && (
          <span
            key={mailboxBumpClass ? `mb-${pulse!.key}` : 'mb'}
            className={`${styles.mailbox} ${mailboxBumpClass}`}
            aria-label={`${worker.mailboxPending} pending messages`}
          >
            <span aria-hidden="true">📩</span>
            {worker.mailboxPending}
          </span>
        )}
      </div>

      <div className={styles.status}>
        <span
          className={`${styles.dot} ${done ? styles.dotDone : STATE_DOT[worker.state]}`}
        />
        <span className={done ? styles.doneLabel : undefined}>
          {done
            ? `done${
                worker.finishedAtMs
                  ? ` · ${formatPing(Date.now() - worker.finishedAtMs)}`
                  : ''
              }`
            : STATE_LABEL[worker.state]}
        </span>
        <span
          className={`${styles.dispatchArrow} ${worker.dispatchOut ? styles.dispatchArrowActive : ''}`}
          aria-hidden="true"
        >
          →
        </span>
        <span
          className={`${styles.dispatchArrow} ${worker.dispatchIn ? styles.dispatchArrowActive : ''}`}
          aria-hidden="true"
        >
          ←
        </span>
      </div>

      <div className={detailClass}>{worker.detail ?? ''}</div>
    </div>
  );
}
