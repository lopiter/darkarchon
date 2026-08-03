import { useState } from 'react';
import { useDashboardStore } from '../../store/dashboard';
import { formatPing } from '../../utils/formatTime';
import styles from './InactiveTeams.module.css';

function size(bytes: number): string {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)}M`;
  return `${Math.round(bytes / 1024)}K`;
}

/**
 * Teams with nothing running, folded away at the bottom of the dashboard.
 *
 * State dirs accumulate — every worktree and every experiment leaves one, and
 * nothing ever removes them. Listing them inline would drown the teams that
 * matter, so they collapse into a single line until asked for. Read-only by
 * design: archiving is `lib/teams.sh archive`, which can check that no tmux
 * session is still alive before moving anything.
 */
export function InactiveTeams() {
  const teams = useDashboardStore((s) => s.inactiveTeams);
  const [open, setOpen] = useState(false);

  if (teams.length === 0) return null;

  return (
    <section className={styles.section}>
      <button
        type="button"
        className={styles.toggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? '▾' : '▸'} inactive teams ({teams.length})
      </button>
      {open && (
        <div className={styles.list}>
          {teams.map((t) => (
            <div key={t.stateDir} className={styles.row}>
              <span className={styles.name}>{t.name}</span>
              <span className={`${styles.tier} ${styles[t.tier] ?? ''}`}>
                {t.tier}
              </span>
              <span className={styles.age}>
                {t.idleSeconds == null ? 'never' : formatPing(t.idleSeconds * 1000)}
                {t.source ? ` · ${t.source}` : ''}
              </span>
              <span className={styles.meta}>
                {t.registeredWorkers}w · {size(t.sizeBytes)}
              </span>
            </div>
          ))}
          <p className={styles.hint}>
            archive with <code>lib/teams.sh archive &lt;team&gt;</code> — moves
            the state dir, never deletes it
          </p>
        </div>
      )}
    </section>
  );
}
