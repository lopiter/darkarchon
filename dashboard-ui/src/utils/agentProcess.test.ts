import { describe, expect, it } from 'vitest';
import { agentIdentity } from './agentProcess';

describe('agentIdentity', () => {
  it('maps claude / codex / grok to C / X / G', () => {
    expect(agentIdentity('claude')).toEqual({ kind: 'claude', letter: 'C', label: 'Claude' });
    expect(agentIdentity('codex')).toEqual({ kind: 'codex', letter: 'X', label: 'Codex' });
    expect(agentIdentity('grok')).toEqual({ kind: 'grok', letter: 'G', label: 'Grok' });
  });

  it('normalizes mixed case and surrounding whitespace', () => {
    expect(agentIdentity(' Claude ')).toEqual({ kind: 'claude', letter: 'C', label: 'Claude' });
    expect(agentIdentity('CODEX')).toEqual({ kind: 'codex', letter: 'X', label: 'Codex' });
    expect(agentIdentity('\tGrok\n')).toEqual({ kind: 'grok', letter: 'G', label: 'Grok' });
  });

  it('returns null for unknown, empty, or missing process so callers draw nothing', () => {
    expect(agentIdentity('hermes')).toBeNull();
    expect(agentIdentity('bash')).toBeNull();
    expect(agentIdentity('claude-code')).toBeNull();
    expect(agentIdentity('')).toBeNull();
    expect(agentIdentity('   ')).toBeNull();
    expect(agentIdentity(null)).toBeNull();
    expect(agentIdentity(undefined)).toBeNull();
  });
});
