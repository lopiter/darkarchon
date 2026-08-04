import { useMemo, useState } from 'react';
import { useClipboard } from '../../hooks/useClipboard';
import { useDashboardStore } from '../../store/dashboard';
import type { InactiveTeam } from '../../types/domain';
import { formatPing } from '../../utils/formatTime';
import styles from './InactiveTeams.module.css';

function size(bytes: number): string {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)}M`;
  return `${Math.round(bytes / 1024)}K`;
}

/**
 * Shell command that archives the given teams.
 *
 * State dirs go in as absolute paths rather than team names: a worktree team
 * named `myteam-feature-x` lives at `myteam/feature-x`, so the name alone
 * cannot locate it. Deliberately without `--yes` — the command still prints
 * what it will move and asks, which is the last check before a batch move.
 */
export function archiveCommand(teams: InactiveTeam[]): string {
  const dirs = teams.map((t) => `'${t.stateDir}'`).join(' ');
  return `$DARKARCHON_HOME/lib/teams.sh archive ${dirs}`;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const { copy, copied } = useClipboard();
  return (
    <button
      type="button"
      className={styles.copy}
      onClick={() => copy(text)}
      title={text}
    >
      {copied ? 'copied' : label}
    </button>
  );
}

/**
 * Teams with nothing running, folded away at the bottom of the dashboard.
 *
 * State dirs accumulate — every worktree and every experiment leaves one, and
 * nothing ever removes them. Listing them inline would drown the teams that
 * matter, so they collapse into a single line until asked for.
 *
 * Grouped by host, and the copy buttons yield a command for one host only: a
 * state dir is visible from the machine holding it, so a command mixing hosts
 * could not run anywhere. Nothing here mutates — archiving stays in the shell,
 * where the script can check that no tmux session is still alive first.
 */
export function InactiveTeams() {
  const teams = useDashboardStore((s) => s.inactiveTeams);
  const [open, setOpen] = useState(false);

  const byHost = useMemo(() => {
    const m = new Map<string, InactiveTeam[]>();
    for (const t of teams) {
      const list = m.get(t.host);
      if (list) list.push(t);
      else m.set(t.host, [t]);
    }
    return Array.from(m.entries());
  }, [teams]);

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
          {byHost.map(([host, hostTeams]) => (
            <div key={host} className={styles.group}>
              <div className={styles.groupHeader}>
                <span className={styles.groupName}>
                  {host} · {hostTeams.length}
                </span>
                <CopyButton
                  text={archiveCommand(hostTeams)}
                  label="copy archive-all"
                />
              </div>
              {hostTeams.map((t) => (
                <div key={t.stateDir} className={styles.row}>
                  <span className={styles.name}>{t.name}</span>
                  <span className={`${styles.tier} ${styles[t.tier] ?? ''}`}>
                    {t.tier}
                  </span>
                  <span className={styles.age}>
                    {t.idleSeconds == null
                      ? 'never'
                      : formatPing(t.idleSeconds * 1000)}
                    {t.source ? ` · ${t.source}` : ''}
                  </span>
                  {/* Spelled out rather than abbreviated: an age column sits
                      right beside this, and "2w" next to "88d" reads as two
                      weeks. */}
                  <span className={styles.meta}>
                    {t.registeredWorkers}{' '}
                    {t.registeredWorkers === 1 ? 'worker' : 'workers'}
                  </span>
                  <span className={styles.meta}>{size(t.sizeBytes)}</span>
                  <CopyButton text={archiveCommand([t])} label="copy" />
                </div>
              ))}
            </div>
          ))}
          <p className={styles.hint}>
            run the copied command on that host — it moves the state dir to{' '}
            <code>~/.darkarchon-archive/</code>, never deletes, and refuses any
            team whose tmux session is still alive
          </p>
        </div>
      )}
    </section>
  );
}
