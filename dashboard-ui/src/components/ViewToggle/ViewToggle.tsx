import { useDashboardStore } from '../../store/dashboard';
import styles from './ViewToggle.module.css';

/**
 * Fixed pill in the bottom-left corner, stacked above ThemeToggle.
 * Switches between the card dashboard and the canvas graph view; the
 * choice persists via the store (localStorage).
 */
export function ViewToggle() {
  const view = useDashboardStore((s) => s.view);
  const setView = useDashboardStore((s) => s.setView);
  const isGraph = view === 'graph';

  return (
    <button
      type="button"
      onClick={() => setView(isGraph ? 'cards' : 'graph')}
      className={styles.toggle}
      title={isGraph ? 'Switch to card view' : 'Switch to graph view'}
      aria-label={isGraph ? 'Switch to card view' : 'Switch to graph view'}
    >
      {isGraph ? '🕸 Graph' : '☰ Cards'}
    </button>
  );
}
