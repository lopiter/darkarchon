import { useEffect, useMemo, useRef, useState } from 'react';
import { useFlipAnimation } from '../../hooks/useFlipAnimation';
import { useDashboardStore } from '../../store/dashboard';
import type { Team, Worker } from '../../types/domain';
import { WorkerRow } from '../WorkerRow/WorkerRow';
import { TeamLabel } from './TeamLabel';
import styles from './TeamSection.module.css';

interface Props {
  hostId: string;
  team: Team;
  showDead: boolean;
  hostStale?: boolean;
}

/** Matches the rowExit keyframes duration (0.3s) so unmount happens after
 * the animation finishes. */
const EXIT_ANIMATION_MS = 300;

export function TeamSection({ hostId, team, showDead, hostStale = false }: Props) {
  const workers = useDashboardStore((s) =>
    s.getSortedTeamWorkers(hostId, team.name, showDead)
  );

  // Carry just-removed workers in render for EXIT_ANIMATION_MS so the
  // rowExit keyframes finish playing before React unmounts them. Their
  // last-known data is stashed in a ref so the exit row keeps its name,
  // state, etc. while fading out.
  const [exitingIds, setExitingIds] = useState<string[]>([]);
  const exitingWorkersRef = useRef<Map<string, Worker>>(new Map());
  const prevIdsRef = useRef<string[]>([]);

  useEffect(() => {
    const currentIds = workers.map((w) => w.id);
    const removed = prevIdsRef.current.filter(
      (id) => !currentIds.includes(id) && !exitingIds.includes(id)
    );

    if (removed.length > 0) {
      // Stash the last-known worker objects so the exit row keeps rendering.
      // Source: previous render of `workers` (kept alive via the ref).
      setExitingIds((prev) => [...prev, ...removed]);
      const t = setTimeout(() => {
        setExitingIds((prev) => prev.filter((id) => !removed.includes(id)));
        removed.forEach((id) => exitingWorkersRef.current.delete(id));
      }, EXIT_ANIMATION_MS);
      prevIdsRef.current = currentIds;
      return () => clearTimeout(t);
    }
    prevIdsRef.current = currentIds;
  }, [workers, exitingIds]);

  // Stash current workers (latest data) so exiting ids can recover them.
  workers.forEach((w) => exitingWorkersRef.current.set(w.id, w));

  const renderList = useMemo(() => {
    const live = workers.map((w) => ({ worker: w, exiting: false }));
    const exiting: { worker: Worker; exiting: true }[] = [];
    for (const id of exitingIds) {
      const w = exitingWorkersRef.current.get(id);
      if (w) exiting.push({ worker: w, exiting: true });
    }
    return [...live, ...exiting];
  }, [workers, exitingIds]);

  const rowsRef = useRef<HTMLDivElement | null>(null);
  useFlipAnimation(
    rowsRef,
    renderList.map((r) => r.worker.id),
    600
  );

  if (renderList.length === 0) return null;

  return (
    <section className={styles.section}>
      <TeamLabel hostId={hostId} team={team} />
      <div ref={rowsRef} className={styles.cards}>
        {renderList.map(({ worker, exiting }) => (
          <WorkerRow
            key={worker.id}
            worker={worker}
            hostStale={hostStale}
            exiting={exiting}
          />
        ))}
      </div>
    </section>
  );
}
