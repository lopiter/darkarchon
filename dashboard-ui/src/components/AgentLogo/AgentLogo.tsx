/**
 * AgentLogo — small monochrome letter badge indicating which agent process
 * is running.
 *
 * Renders at 16×16px by default; pass `size` to override.
 * Falls back to null for unknown/empty process values — callers render
 * plain text in that case.
 *
 * Neutral first-letter badges (no third-party logos / trademarks):
 *   - Claude: 'C'
 *   - Codex:  'X'
 */

import styles from './AgentLogo.module.css';

interface Props {
  /** worker.process value — 'claude' | 'codex' | anything else */
  process: string;
  size?: number;
  className?: string;
}

export function AgentLogo({ process, size = 16, className }: Props) {
  const normalized = process?.toLowerCase().trim();

  const badge = (kind: 'claude' | 'codex', letter: string, label: string) => (
    <span
      className={`${styles.logo} ${styles[kind]} ${className ?? ''}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.64) }}
      title={label}
      aria-label={`${label} agent`}
    >
      {letter}
    </span>
  );

  if (normalized === 'claude') return badge('claude', 'C', 'Claude');
  if (normalized === 'codex') return badge('codex', 'X', 'Codex');

  return null;
}
