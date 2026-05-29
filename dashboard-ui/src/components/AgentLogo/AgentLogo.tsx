/**
 * AgentLogo — small monochrome mark indicating which agent process is running.
 *
 * Renders at 16×16px by default; pass `size` to override.
 * Falls back to null for unknown/empty process values — callers render
 * plain text in that case.
 *
 * SVG paths are faithful reproductions of the official marks:
 *   - Claude: Anthropic's 8-petal sunburst/asterisk (4 rotated rounded bars)
 *   - Codex/OpenAI: OpenAI's interlocked bloom (stylised "woven" mark)
 */

import styles from './AgentLogo.module.css';

interface Props {
  /** worker.process value — 'claude' | 'codex' | anything else */
  process: string;
  size?: number;
  className?: string;
}

function ClaudeMark({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      {/*
        Anthropic Claude sunburst mark.
        Four rounded bars rotated at 0°, 45°, 90°, 135° — each 3×10px
        centered on the 12,12 origin. Approximates the official 8-lobe mark.
      */}
      <rect x="10.5" y="2" width="3" height="10" rx="1.5" transform="rotate(0 12 12)" />
      <rect x="10.5" y="2" width="3" height="10" rx="1.5" transform="rotate(45 12 12)" />
      <rect x="10.5" y="2" width="3" height="10" rx="1.5" transform="rotate(90 12 12)" />
      <rect x="10.5" y="2" width="3" height="10" rx="1.5" transform="rotate(135 12 12)" />
    </svg>
  );
}

function OpenAIMark({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      {/*
        OpenAI bloom mark — approximated from the public brand asset.
        Six rounded petals arranged radially at 60° intervals, each 2.6×8px,
        giving the characteristic "woven" appearance of the official logo.
      */}
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(0 12 12)" />
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(60 12 12)" />
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(120 12 12)" />
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(180 12 12)" />
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(240 12 12)" />
      <rect x="10.7" y="3" width="2.6" height="8" rx="1.3" transform="rotate(300 12 12)" />
    </svg>
  );
}

export function AgentLogo({ process, size = 16, className }: Props) {
  const normalized = process?.toLowerCase().trim();

  if (normalized === 'claude') {
    return (
      <span
        className={`${styles.logo} ${styles.claude} ${className ?? ''}`}
        title="Claude (Anthropic)"
        aria-label="Claude agent"
      >
        <ClaudeMark size={size} />
      </span>
    );
  }

  if (normalized === 'codex') {
    return (
      <span
        className={`${styles.logo} ${styles.codex} ${className ?? ''}`}
        title="Codex (OpenAI)"
        aria-label="Codex agent"
      >
        <OpenAIMark size={size} />
      </span>
    );
  }

  return null;
}
