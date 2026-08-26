/**
 * Shared agent-process identity for Cards (AgentLogo) and Graph (canvas chip).
 *
 * Unknown / empty process values return null so callers render nothing.
 */

export type AgentKind = 'claude' | 'codex' | 'grok';

export interface AgentIdentity {
  kind: AgentKind;
  letter: 'C' | 'X' | 'G';
  label: string;
}

const BY_KIND: Record<AgentKind, AgentIdentity> = {
  claude: { kind: 'claude', letter: 'C', label: 'Claude' },
  codex: { kind: 'codex', letter: 'X', label: 'Codex' },
  grok: { kind: 'grok', letter: 'G', label: 'Grok' },
};

export function agentIdentity(process: string | null | undefined): AgentIdentity | null {
  const normalized = (process ?? '').toLowerCase().trim();
  if (normalized === 'claude' || normalized === 'codex' || normalized === 'grok') {
    return BY_KIND[normalized];
  }
  return null;
}
