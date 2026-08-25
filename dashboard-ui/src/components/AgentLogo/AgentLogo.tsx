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
 *   - Grok:   'G'
 */

import { agentIdentity } from '../../utils/agentProcess';
import styles from './AgentLogo.module.css';

interface Props {
  /** worker.process value — 'claude' | 'codex' | 'grok' | anything else */
  process: string;
  size?: number;
  className?: string;
}

export function AgentLogo({ process, size = 16, className }: Props) {
  const ident = agentIdentity(process);
  if (!ident) return null;

  return (
    <span
      className={`${styles.logo} ${styles[ident.kind]} ${className ?? ''}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.64) }}
      title={ident.label}
      aria-label={`${ident.label} agent`}
    >
      {ident.letter}
    </span>
  );
}
