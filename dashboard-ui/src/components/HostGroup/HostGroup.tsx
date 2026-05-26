import { useMemo } from 'react';
import { useDashboardStore } from '../../store/dashboard';
import type { Host } from '../../types/domain';
import { isHostStale } from '../../utils/transform';
import { isHidden } from '../../utils/visibility';
import { TeamSection } from '../TeamSection/TeamSection';
import { HostHeader } from './HostHeader';
import styles from './HostGroup.module.css';

interface Props {
  host: Host;
}

export function HostGroup({ host }: Props) {
  const hiddenExpanded = useDashboardStore(
    (s) => Boolean(s.hiddenExpanded[host.id])
  );
  const toggleHidden = useDashboardStore((s) => s.toggleHidden);
  const deadSince = useDashboardStore((s) => s.deadSince);

  const workerCount = host.teams.reduce((n, t) => n + t.workers.length, 0);
  const stale = isHostStale(host);

  // Count of dead/unknown workers older than HIDDEN_CUTOFF_MS — the
  // bucket that the "N hidden" toggle reveals. Recomputed only when
  // hosts or deadSince change (polling tick triggers either way).
  const hiddenCount = useMemo(() => {
    const now = Date.now();
    let count = 0;
    for (const t of host.teams) {
      for (const w of t.workers) {
        if (isHidden(w.state, w.id, deadSince, now)) count++;
      }
    }
    return count;
  }, [host, deadSince]);

  return (
    <section className={`${styles.group} ${stale ? styles.hostStale : ''}`.trim()}>
      <HostHeader
        hostname={host.id}
        workerCount={workerCount}
        teamCount={host.teams.length}
        lastPingMs={host.lastPingMs}
        isStale={stale}
        hiddenCount={hiddenCount}
        hiddenExpanded={hiddenExpanded}
        onToggleHidden={() => toggleHidden(host.id)}
      />
      <div className={styles.teams}>
        {host.teams.map((team) => (
          <TeamSection
            key={team.name}
            hostId={host.id}
            team={team}
            showDead={true}
            hostStale={stale}
          />
        ))}
      </div>
    </section>
  );
}
