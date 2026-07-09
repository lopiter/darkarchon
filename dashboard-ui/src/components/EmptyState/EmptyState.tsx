/**
 * DESIGN.md Section 8 — three empty-state variants.
 *
 *   variant="no-workers"   no workers anywhere (Section 8.1, first-run)
 *   variant="all-stale"    every host stale (Section 8.3, warning tone)
 *
 * Per-host "host present but no workers" (Section 8.2) is rendered
 * inline by HostGroup so the host header stays visible.
 */

import styles from './EmptyState.module.css';

interface Props {
  variant: 'no-workers' | 'all-stale';
  hostCount?: number;
}

export function EmptyState({ variant, hostCount = 0 }: Props) {
  if (variant === 'all-stale') {
    return (
      <div className={`${styles.wrap} ${styles.warn}`}>
        <div className={styles.card}>
          <div className={styles.headline}>
            All hosts are unresponsive
          </div>
          <div className={styles.body}>
            Check that the agent is running. Hosts are marked stale once
            more than 15s have passed since their last ping.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.headline}>No workers found yet</div>
        <div className={styles.body}>
          darkarchon automatically discovers Claude running in your tmux
          sessions. Start Claude Code as usual on any machine and it will
          appear here.
        </div>
        <div className={styles.meta}>
          <span>Connected hosts: {hostCount}</span>
        </div>
      </div>
    </div>
  );
}
