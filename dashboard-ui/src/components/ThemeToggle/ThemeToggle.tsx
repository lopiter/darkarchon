import { useTheme } from '../../hooks/useTheme';
import styles from './ThemeToggle.module.css';

/**
 * Fixed pill in the bottom-left corner (stacked above NotificationToggle).
 * Toggles between the dark default and the light theme; the choice persists
 * via useTheme (localStorage).
 */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isDark = theme === 'dark';

  return (
    <button
      type="button"
      onClick={toggle}
      className={styles.toggle}
      title={isDark ? '라이트 모드로 전환' : '다크 모드로 전환'}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {isDark ? '🌙 Dark' : '☀️ Light'}
    </button>
  );
}
