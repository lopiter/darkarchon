/**
 * Fixed top-right chip summarizing what needs the user across the whole
 * fleet: ⚠ workers blocked on input, ✓ finished results not yet reviewed.
 *
 * The same counts go into document.title so the browser tab shows them even
 * when the dashboard isn't the focused window — the user usually isn't
 * watching this tab, which is the whole reason unseen-done tracking exists.
 *
 * Hidden entirely when both counts are zero: quiet by default, visible only
 * when there is something to triage.
 */

import { useEffect } from 'react';
import { useDashboardStore } from '../../store/dashboard';
import styles from './AttentionSummary.module.css';

const BASE_TITLE = 'darkarchon';

export function AttentionSummary() {
  const hosts = useDashboardStore((s) => s.hosts);
  const ackAll = useDashboardStore((s) => s.ackAll);

  let awaiting = 0;
  let done = 0;
  for (const h of hosts) {
    for (const t of h.teams) {
      for (const w of t.workers) {
        if (w.state === 'dead' || w.state === 'unknown') continue;
        if (w.state.startsWith('awaiting_user:')) awaiting += 1;
        if (w.unseenDone) done += 1;
      }
    }
  }

  useEffect(() => {
    const parts = [];
    if (awaiting > 0) parts.push(`⚠${awaiting}`);
    if (done > 0) parts.push(`✓${done}`);
    document.title = parts.length
      ? `${parts.join(' ')} · ${BASE_TITLE}`
      : BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [awaiting, done]);

  if (awaiting === 0 && done === 0) return null;

  return (
    <div className={styles.chip} role="status">
      {awaiting > 0 && (
        <span className={styles.awaiting}>⚠ {awaiting} awaiting</span>
      )}
      {awaiting > 0 && done > 0 && <span className={styles.sep}>·</span>}
      {done > 0 && (
        <>
          <span className={styles.done}>✓ {done} done</span>
          <button
            type="button"
            className={styles.clear}
            onClick={ackAll}
            title="Mark every finished result as reviewed"
          >
            clear
          </button>
        </>
      )}
    </div>
  );
}
