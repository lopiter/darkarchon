import { describe, expect, it } from 'vitest';
import type { Worker } from '../types/domain';
import { restoreCommand, restorePlan } from './teamRestore';

function w(overrides: Partial<Worker> = {}): Worker {
  return {
    id: 'h:myteam:1.1',
    name: 'alpha',
    state: 'idle',
    role: '',
    tmuxTarget: 'myteam:1.1',
    process: 'claude',
    external: false,
    isOrchestrator: false,
    unseenDone: false,
    enteredStateAt: '2026-05-23T15:00:00Z',
    dispatchOut: false,
    dispatchIn: false,
    mailboxPending: 0,
    incomingDispatches: [],
    outgoingDispatches: [],
    mailboxSenders: [],
    recentTasks: [],
    ...overrides,
  };
}

const DIR = '/home/me/.darkarchon/myteam';

describe('restorePlan', () => {
  it('names only the dead workers, sorted', () => {
    const plan = restorePlan(
      [
        w({ name: 'zeta', state: 'dead' }),
        w({ name: 'alpha', state: 'dead' }),
        w({ name: 'busy', state: 'busy' }),
        w({ name: 'idle', state: 'idle' }),
      ],
      DIR
    );
    expect(plan.dead).toEqual(['alpha', 'zeta']);
  });

  it('is empty for a team with every pane alive', () => {
    const plan = restorePlan([w({ state: 'idle' }), w({ state: 'busy' })], DIR);
    expect(plan.dead).toEqual([]);
    expect(plan.fresh).toEqual([]);
  });

  it('flags invited and non-claude dead workers as coming back fresh', () => {
    const plan = restorePlan(
      [
        w({ name: 'invited', state: 'dead', external: true }),
        w({ name: 'codex', state: 'dead', process: 'codex' }),
        w({ name: 'spawned', state: 'dead' }),
        // alive ones never count, whatever they are
        w({ name: 'live-invited', state: 'idle', external: true }),
      ],
      DIR
    );
    expect(plan.fresh).toEqual(['codex', 'invited']);
    expect(plan.dead).toEqual(['codex', 'invited', 'spawned']);
  });

  it('addresses restore-team.sh by state dir, quoted for the shell', () => {
    const plan = restorePlan([w({ state: 'dead' })], "/home/o'brien/.darkarchon/t");
    expect(plan.command).toBe(
      `EE_STATE_DIR='/home/o'\\''brien/.darkarchon/t' "$DARKARCHON_HOME/restore-team.sh"`
    );
    expect(plan.dryRun).toBe(`${plan.command} --dry-run`);
  });

  it('has no command when the hub reported no state dir', () => {
    const plan = restorePlan([w({ state: 'dead' })], undefined);
    expect(plan.dead).toEqual(['alpha']);
    expect(plan.command).toBeNull();
    expect(plan.dryRun).toBeNull();
  });
});

describe('restoreCommand', () => {
  it('is the same command the panel offers, without needing a worker list', () => {
    expect(restoreCommand('/s/t')).toBe(restorePlan([w({ state: 'dead' })], '/s/t').command);
  });
});
