import { useDashboardStore } from '../../store/dashboard';
import type { Team } from '../../types/domain';
import { formatPing } from '../../utils/formatTime';
import { shutdownBlockers } from '../../utils/teamShutdown';
import { onTeamContextMenu } from '../ContextMenu/menus';
import styles from './TeamSection.module.css';

interface Props {
  hostId: string;
  team: Team;
}

/**
 * A team whose workers are running right now needs no age annotation — the
 * cards below it already say so. The badge only earns its space once a team
 * has gone quiet, and it names the signal so "quiet since its last dispatch"
 * reads differently from "registered and never used".
 *
 * The label is also the team's handle: clicking opens the team panel (roster +
 * wind-down check + shutdown commands), right-clicking gets the same commands
 * without the trip through the panel. A ✓ appears once nothing in the team is
 * in flight — the one-glance answer to "can I stop this yet?", so that
 * question doesn't require opening anything at all.
 */
export function TeamLabel({ hostId, team }: Props) {
  const selectTeam = useDashboardStore((s) => s.selectTeam);
  const activity = team.activity;
  const idle = activity?.idleSeconds;
  const showAge = activity && activity.tier !== 'live' && idle != null;
  const done = team.workers.length > 0 && shutdownBlockers(team.workers).length === 0;

  return (
    <div className={styles.label}>
      <button
        type="button"
        className={styles.labelButton}
        onClick={() => selectTeam(hostId, team.name)}
        onContextMenu={onTeamContextMenu(hostId, team)}
        title="team detail — roster, wind-down check, shutdown commands"
      >
        {team.name}
      </button>
      {done && (
        <span
          className={styles.doneMark}
          title="nothing in flight — safe to stop"
        >
          ✓
        </span>
      )}
      {showAge && (
        <span
          className={`${styles.badge} ${styles[activity.tier] ?? ''}`}
          title={`last activity: ${activity.source ?? 'unknown'}`}
        >
          {formatPing(idle * 1000)}
          {activity.source ? ` · ${activity.source}` : ''}
        </span>
      )}
    </div>
  );
}
