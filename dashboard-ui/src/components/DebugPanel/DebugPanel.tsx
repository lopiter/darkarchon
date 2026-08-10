/**
 * DEV-only trigger panel for visually verifying Phase 2 notifications.
 * Removed from production bundle by tree-shaking (early `return null` on
 * !import.meta.env.DEV — Vite eliminates the dead branch in build mode).
 *
 * Positioning: bottom-right fixed, collapsed by default to avoid covering
 * any awaiting cards. Click the chip to expand.
 */

import { useState } from 'react';
import { useDashboardStore } from '../../store/dashboard';
import type { Host, Worker, WorkerState } from '../../types/domain';
import styles from './DebugPanel.module.css';

type WorkerMutator = (w: Worker) => Worker;

function mutateWorkerByName(
  hosts: Host[],
  workerName: string,
  fn: WorkerMutator
): Host[] {
  return hosts.map((h) => ({
    ...h,
    teams: h.teams.map((t) => ({
      ...t,
      workers: t.workers.map((w) => (w.name === workerName ? fn(w) : w)),
    })),
  }));
}

function removeWorkerByName(hosts: Host[], workerName: string): Host[] {
  return hosts.map((h) => ({
    ...h,
    teams: h.teams.map((t) => ({
      ...t,
      workers: t.workers.filter((w) => w.name !== workerName),
    })),
  }));
}

function addWorker(
  hosts: Host[],
  hostId: string,
  teamName: string,
  worker: Worker
): Host[] {
  let added = false;
  const next = hosts.map((h) => {
    if (h.id !== hostId) return h;
    return {
      ...h,
      teams: h.teams.map((t) => {
        if (t.name !== teamName) return t;
        if (t.workers.some((w) => w.id === worker.id)) return t;
        added = true;
        return { ...t, workers: [...t.workers, worker] };
      }),
    };
  });
  return added ? next : hosts;
}

function withFreshHosts(mutate: (hosts: Host[]) => Host[]): void {
  const { hosts, setHosts } = useDashboardStore.getState();
  setHosts(mutate(hosts));
}

function biStepWorker(workerName: string, intermediate: WorkerMutator, target: WorkerMutator) {
  withFreshHosts((hosts) => mutateWorkerByName(hosts, workerName, intermediate));
  setTimeout(() => {
    withFreshHosts((hosts) => mutateWorkerByName(hosts, workerName, target));
  }, 50);
}

const setState =
  (state: WorkerState): WorkerMutator =>
  (w) => ({ ...w, state });

const setFlag =
  <K extends 'dispatchOut' | 'dispatchIn'>(key: K, value: boolean): WorkerMutator =>
  (w) => ({ ...w, [key]: value });

const bumpMailbox: WorkerMutator = (w) => ({
  ...w,
  mailboxPending: w.mailboxPending + 1,
});

const SPAWN_WORKER: Worker = {
  id: 'main:myteam:qa-agent',
  name: 'qa-agent',
  state: 'busy',
  role: 'qa',
  tmuxTarget: 'myteam:9.1',
  process: 'claude',
  external: false,
  isOrchestrator: false,
  unseenDone: false,
  enteredStateAt: new Date().toISOString(),
  dispatchOut: false,
  dispatchIn: false,
  mailboxPending: 0,
  incomingDispatches: [],
  outgoingDispatches: [],
  mailboxSenders: [],
  recentTasks: [],
  detail: 'Running tests…',
};

export function DebugPanel() {
  const [expanded, setExpanded] = useState(false);
  if (!import.meta.env.DEV) return null;

  const actions: Array<{ label: string; fn: () => void }> = [
    {
      label: '→ awaiting (frontend)',
      fn: () =>
        biStepWorker(
          'frontend',
          setState('busy'),
          setState('awaiting_user:typed')
        ),
    },
    {
      label: '→ rate_limited (main)',
      fn: () =>
        biStepWorker('main', setState('busy'), setState('rate_limited')),
    },
    {
      label: '→ dispatch_out (main)',
      fn: () =>
        biStepWorker(
          'main',
          setFlag('dispatchOut', false),
          setFlag('dispatchOut', true)
        ),
    },
    {
      label: '→ dispatch_in (writer)',
      fn: () =>
        biStepWorker(
          'writer',
          setFlag('dispatchIn', false),
          setFlag('dispatchIn', true)
        ),
    },
    {
      label: 'mailbox +1 (writer)',
      fn: () => withFreshHosts((hosts) => mutateWorkerByName(hosts, 'writer', bumpMailbox)),
    },
    {
      label: 'kill worker (sandbox)',
      fn: () => withFreshHosts((hosts) => removeWorkerByName(hosts, 'sandbox')),
    },
    {
      label: 'spawn worker (qa-agent)',
      fn: () =>
        withFreshHosts((hosts) =>
          addWorker(hosts, 'main', 'myteam', SPAWN_WORKER)
        ),
    },
  ];

  return (
    <aside
      className={`${styles.panel} ${expanded ? styles.expanded : styles.collapsed}`}
      aria-label="Phase 2 debug panel"
    >
      <button
        type="button"
        className={styles.chip}
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        PHASE 2 DEBUG {expanded ? '▾' : '▴'}
      </button>
      {expanded && (
        <div className={styles.actions}>
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={a.fn}
              className={styles.button}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}
