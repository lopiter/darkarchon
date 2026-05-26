import { formatPing } from '../../utils/formatTime';
import { HiddenToggle } from './HiddenToggle';
import styles from './HostGroup.module.css';

interface Props {
  hostname: string;
  workerCount: number;
  teamCount: number;
  lastPingMs: number;
  isStale: boolean;
  hiddenCount: number;
  hiddenExpanded: boolean;
  onToggleHidden: () => void;
}

export function HostHeader({
  hostname,
  workerCount,
  teamCount,
  lastPingMs,
  isStale,
  hiddenCount,
  hiddenExpanded,
  onToggleHidden,
}: Props) {
  return (
    <header className={styles.header}>
      <div className={styles.headerLeft}>
        <span className={styles.icon} aria-hidden="true">
          🖥
        </span>
        <span className={`${styles.hostname} ${isStale ? styles.hostStale : ''}`}>
          {hostname}
        </span>
      </div>
      <div className={styles.headerRight}>
        <span className={styles.meta}>
          {workerCount} {workerCount === 1 ? 'worker' : 'workers'} ·{' '}
          {teamCount} {teamCount === 1 ? 'team' : 'teams'} · last ping{' '}
          <span className={isStale ? styles.pingStale : ''}>
            {formatPing(lastPingMs)}
          </span>
        </span>
        <HiddenToggle
          count={hiddenCount}
          expanded={hiddenExpanded}
          onToggle={onToggleHidden}
        />
      </div>
    </header>
  );
}
