/**
 * Team view of the detail panel — "is this team done, and how do I end it?"
 *
 * The dashboard already shows worker states one row at a time; what it could
 * not answer before was the question you actually have at the end of a batch:
 * *is anything still in flight anywhere in this team?* A row-by-row scan gets
 * that wrong the moment a team has more rows than fit on screen, and the cost
 * of getting it wrong is a killed session that took a half-finished turn with
 * it. So this panel states the verdict first and the evidence under it.
 *
 * It never runs anything. Shutdown is a paste — see utils/teamShutdown.ts.
 */

import { useMemo } from 'react';
import { useClipboard } from '../../hooks/useClipboard';
import { useDashboardStore } from '../../store/dashboard';
import type { Team, Worker } from '../../types/domain';
import { formatPing } from '../../utils/formatTime';
import { sortWorkersForTeam } from '../../utils/sortWorkers';
import {
  externalSessions,
  ownedSessions,
  shutdownBlockers,
  teamCommands,
} from '../../utils/teamShutdown';
import { AgentLogo } from '../AgentLogo/AgentLogo';
import { onWorkerContextMenu } from '../ContextMenu/menus';
import styles from './DetailPanel.module.css';
import tp from './TeamPanel.module.css';

/** Resolve the selected team out of the host tree. Returns null once the team
 *  empties out — the panel closes itself rather than showing a husk. */
export function useSelectedTeam(): { host: string; team: Team } | null {
  const sel = useDashboardStore((s) => s.selectedTeam);
  const hosts = useDashboardStore((s) => s.hosts);
  return useMemo(() => {
    if (!sel) return null;
    const host = hosts.find((h) => h.id === sel.host);
    const team = host?.teams.find((t) => t.name === sel.team);
    return host && team ? { host: host.id, team } : null;
  }, [sel, hosts]);
}

export function TeamPanelContent({
  host,
  team,
  onClose,
}: {
  host: string;
  team: Team;
  onClose: () => void;
}) {
  const workers = team.workers;
  const blockers = useMemo(() => shutdownBlockers(workers), [workers]);
  const cmds = useMemo(
    () => teamCommands(workers, team.stateDir),
    [workers, team.stateDir]
  );
  const borrowed = useMemo(() => externalSessions(workers), [workers]);
  const owned = useMemo(() => ownedSessions(workers), [workers]);
  const ready = blockers.length === 0;

  return (
    <>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.headerName}>
            {team.name}
            <span
              className={`${styles.statePill} ${ready ? tp.pillReady : styles.amber}`}
            >
              {ready ? 'ready to stop' : 'still running'}
            </span>
          </span>
          <span className={styles.headerMeta}>
            {host} · {workers.length}{' '}
            {workers.length === 1 ? 'worker' : 'workers'}
            {team.activity?.idleSeconds != null &&
              ` · idle ${formatPing(team.activity.idleSeconds * 1000)}`}
          </span>
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

      <ReadinessSection blockers={blockers} />
      <RosterSection workers={workers} />
      <ShutdownSection
        cmds={cmds}
        host={host}
        owned={owned}
        borrowed={borrowed}
        ready={ready}
        stateDir={team.stateDir}
      />
    </>
  );
}

function ReadinessSection({
  blockers,
}: {
  blockers: ReturnType<typeof shutdownBlockers>;
}) {
  if (blockers.length === 0) {
    return (
      <section className={`${styles.section} ${tp.ready}`}>
        <div className={styles.sectionLabel}>Wind-down check</div>
        <div className={tp.verdictOk}>
          ✓ nothing in flight — safe to stop
        </div>
        <span className={styles.tmuxHint}>
          no worker is busy, waiting, or holding an unread result
        </span>
      </section>
    );
  }
  return (
    <section className={`${styles.section} ${styles.awaiting}`}>
      <div className={styles.sectionLabel}>Wind-down check</div>
      <div className={tp.verdictBusy}>
        stopping now would interrupt work
      </div>
      <div className={tp.blockers}>
        {blockers.map((b) => (
          <div key={b.kind} className={tp.blockerRow}>
            <span className={tp.blockerLabel}>{b.label}</span>
            <span className={tp.blockerWho}>{b.workers.join(', ')}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function RosterSection({ workers }: { workers: Worker[] }) {
  const selectWorker = useDashboardStore((s) => s.selectWorker);
  const sorted = useMemo(() => sortWorkersForTeam(workers, true), [workers]);
  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>
        Workers<span>{workers.length}</span>
      </div>
      <div className={tp.roster}>
        {sorted.map((w) => (
          <button
            key={w.id}
            type="button"
            className={tp.rosterRow}
            onClick={() => selectWorker(w.id)}
            onContextMenu={onWorkerContextMenu(w)}
          >
            <AgentLogo process={w.process} size={12} />
            <span className={tp.rosterName}>{w.name}</span>
            {w.external && <span className={tp.extTag}>INVITED</span>}
            <span className={tp.rosterTarget}>{w.tmuxTarget}</span>
            <span className={tp.rosterState}>
              {w.unseenDone && w.state === 'idle' ? 'done' : w.state}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ShutdownSection({
  cmds,
  host,
  owned,
  borrowed,
  ready,
  stateDir,
}: {
  cmds: ReturnType<typeof teamCommands>;
  host: string;
  owned: string[];
  borrowed: string[];
  ready: boolean;
  stateDir?: string;
}) {
  return (
    <section className={styles.section}>
      {/* Named, not implied: a state dir and a tmux session only exist on the
          machine that owns them, so a command copied here runs nowhere else. */}
      <div className={styles.sectionLabel}>
        Shutdown<span>run on {host}</span>
      </div>

      {owned.length === 0 ? (
        <span className={styles.tmuxHint}>
          this team owns no tmux session of its own — every worker in it was
          invited, so there is nothing here to kill
        </span>
      ) : (
        <CommandBlock
          label={`1 · stop session${owned.length > 1 ? 's' : ''}`}
          command={cmds.stop}
          danger
          warn={ready ? null : 'work is still in flight — see the check above'}
        />
      )}

      {cmds.prune && (
        <CommandBlock
          label="2 · prune dead registrations"
          command={cmds.prune}
          note="killing the session leaves the names registered; until they are pruned the same worker cannot be spawned again"
        />
      )}

      {cmds.archive ? (
        <CommandBlock
          label="3 · archive the state dir"
          command={cmds.archive}
          note="moves it to ~/.darkarchon-archive/, never deletes, and refuses while any of the team's sessions is still alive"
        />
      ) : (
        !stateDir && (
          <span className={styles.tmuxHint}>
            the hub reported no state dir for this team, so prune and archive
            cannot be addressed to it
          </span>
        )
      )}

      {/* Only worth its space once there is more than one step to chain. */}
      {[cmds.stop, cmds.prune, cmds.archive].filter(Boolean).length > 1 && (
        <CopyRow label="copy the whole sequence" command={cmds.full} />
      )}

      {borrowed.length > 0 && (
        <div className={tp.warn}>
          <strong>invited panes stay up.</strong> {borrowed.join(', ')}{' '}
          {borrowed.length === 1 ? 'is' : 'are'} someone else's session — the
          commands above deliberately leave{' '}
          {borrowed.length === 1 ? 'it' : 'them'} alone. Use{' '}
          <code>uninvite-worker.sh</code> to drop{' '}
          {borrowed.length === 1 ? 'it' : 'them'} from the team.
        </div>
      )}
    </section>
  );
}

function CommandBlock({
  label,
  command,
  note,
  warn,
  danger,
}: {
  label: string;
  command: string;
  note?: string;
  warn?: string | null;
  danger?: boolean;
}) {
  const { copy, copied, failed } = useClipboard();
  return (
    <div className={tp.cmdBlock}>
      <div className={tp.cmdHead}>
        <span className={danger ? tp.cmdLabelDanger : tp.cmdLabel}>
          {label}
        </span>
        <button
          type="button"
          className={tp.cmdCopy}
          onClick={() => copy(command)}
        >
          {copied ? '✓ copied' : failed ? '✗ select it below' : '📋 copy'}
        </button>
      </div>
      <code className={tp.cmd}>{command}</code>
      {warn && <span className={tp.cmdWarn}>{warn}</span>}
      {note && <span className={styles.tmuxHint}>{note}</span>}
    </div>
  );
}

function CopyRow({ label, command }: { label: string; command: string }) {
  const { copy, copied, failed } = useClipboard();
  return (
    <button
      type="button"
      className={tp.copyAll}
      onClick={() => copy(command)}
    >
      {copied ? '✓ copied' : failed ? '✗ copy failed' : label}
    </button>
  );
}
