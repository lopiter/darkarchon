import { useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'darkarchon-theme';

/** Read the persisted choice; default to dark (the dashboard's original look). */
function readInitial(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* localStorage unavailable (private mode / SSR) — fall through to default */
  }
  return 'dark';
}

/**
 * Theme state + persistence. Applies `data-theme` on <html> so tokens.css can
 * override color tokens for light mode; dark is the default (no attribute / dark
 * values in :root). Returns the current theme and a toggle.
 */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(readInitial);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore persistence failures */
    }
  }, [theme]);

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));
  return { theme, toggle };
}
