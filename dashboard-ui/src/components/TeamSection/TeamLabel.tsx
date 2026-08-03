import type { TeamActivity } from '../../types/domain';
import { formatPing } from '../../utils/formatTime';
import styles from './TeamSection.module.css';

interface Props {
  name: string;
  activity?: TeamActivity;
}

/**
 * A team whose workers are running right now needs no age annotation — the
 * cards below it already say so. The badge only earns its space once a team
 * has gone quiet, and it names the signal so "quiet since its last dispatch"
 * reads differently from "registered and never used".
 */
export function TeamLabel({ name, activity }: Props) {
  const idle = activity?.idleSeconds;
  const showAge = activity && activity.tier !== 'live' && idle != null;

  return (
    <div className={styles.label}>
      {name}
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
