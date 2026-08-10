import { useCardKeyboardNav } from '../../hooks/useCardKeyboardNav';
import { useDashboardStore } from '../../store/dashboard';
import { isHostStale } from '../../utils/transform';
import { EmptyState } from '../EmptyState/EmptyState';
import { HostGroup } from '../HostGroup/HostGroup';
import { InactiveTeams } from '../InactiveTeams/InactiveTeams';
import { NotificationToggle } from '../NotificationToggle/NotificationToggle';
import { ThemeToggle } from '../ThemeToggle/ThemeToggle';
import styles from './Dashboard.module.css';

export function Dashboard() {
  const hosts = useDashboardStore((s) => s.hosts);
  const panelOpen = useDashboardStore(
    (s) => s.selectedWorkerId !== null || s.selectedTeam !== null
  );
  useCardKeyboardNav(!panelOpen);

  const totalWorkers = hosts.reduce(
    (n, h) => n + h.teams.reduce((m, t) => m + t.workers.length, 0),
    0
  );
  const allStale = hosts.length > 0 && hosts.every(isHostStale);

  let body: React.ReactNode;
  if (totalWorkers === 0 && !allStale) {
    body = <EmptyState variant="no-workers" hostCount={hosts.length} />;
  } else if (allStale) {
    body = <EmptyState variant="all-stale" hostCount={hosts.length} />;
  } else {
    body = hosts.map((host) => <HostGroup key={host.id} host={host} />);
  }

  return (
    <>
      <ThemeToggle />
      <NotificationToggle />
      <main
        className={`${styles.dashboard} ${panelOpen ? styles.panelOpen : ''}`}
      >
        {body}
        <InactiveTeams />
      </main>
    </>
  );
}
