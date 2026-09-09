import { describe, expect, it } from 'vitest';
import { agentBadgeTitle, agentIdentity } from './agentProcess';

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

describe('agentBadgeTitle', () => {
  it('names the agent when the registration is trusted', () => {
    expect(agentBadgeTitle('claude')).toBe('Claude');
    expect(agentBadgeTitle('grok')).toBe('Grok');
  });

  it('spells out what contradicts the record when the registration is stale', () => {
    // Naming the observed process matters: the badge cannot know WHICH agent
    // replaced the old one, only that the recorded one is ruled out.
    expect(agentBadgeTitle('grok', '2.1.263')).toBe(
      'Recorded as Grok, but this pane runs "2.1.263" — registration is stale',
    );
  });

  it('returns null for an unknown process, conflict or not', () => {
    expect(agentBadgeTitle('hermes')).toBeNull();
    expect(agentBadgeTitle('hermes', '2.1.263')).toBeNull();
  });
});
