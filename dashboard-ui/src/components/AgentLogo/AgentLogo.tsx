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
 *   - Gemini: 'M' (G is taken; the tooltip spells it out)
 */

import { agentBadgeTitle, agentIdentity } from '../../utils/agentProcess';
import styles from './AgentLogo.module.css';

interface Props {
  /** worker.process value — 'claude' | 'codex' | 'grok' | 'gemini' | anything else */
  process: string;
  /**
   * worker.kindConflict — the pane's own process name when it rules out the
   * recorded kind. Rings the badge amber and explains itself in the tooltip.
   */
  conflict?: string;
  size?: number;
  className?: string;
}

export function AgentLogo({ process, conflict, size = 16, className }: Props) {
  const ident = agentIdentity(process);
  if (!ident) return null;

  const title = agentBadgeTitle(process, conflict) ?? ident.label;
  return (
    <span
      className={[styles.logo, styles[ident.kind], conflict ? styles.conflict : '', className ?? '']
        .filter(Boolean)
        .join(' ')}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.64) }}
      title={title}
      aria-label={conflict ? title : `${ident.label} agent`}
    >
      {ident.letter}
    </span>
  );
}
