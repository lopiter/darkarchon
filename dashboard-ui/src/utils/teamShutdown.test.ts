import { describe, expect, it } from 'vitest';
import type { Worker } from '../types/domain';
import {
  externalSessions,
  ownedSessions,
  shutdownBlockers,
  teamCommands,
} from './teamShutdown';

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

describe('shutdownBlockers', () => {
  it('clears an all-idle team', () => {
    expect(shutdownBlockers([w(), w({ id: 'b', name: 'beta' })])).toEqual([]);
  });

  it('does not count dead workers — those are why you are stopping', () => {
    expect(shutdownBlockers([w({ state: 'dead' }), w({ state: 'unknown' })])).toEqual(
      []
    );
  });

  it('names the workers behind each reason', () => {
    const blockers = shutdownBlockers([
      w({ name: 'alpha', state: 'busy' }),
      w({ name: 'beta', state: 'awaiting_user:permission' }),
      w({ name: 'gamma' }),
    ]);
    expect(blockers.map((b) => b.kind)).toEqual(['working', 'awaiting']);
    expect(blockers[0]!.workers).toEqual(['alpha']);
    expect(blockers[1]!.workers).toEqual(['beta']);
  });

  it('blocks on an unread result even when every worker is idle', () => {
    const blockers = shutdownBlockers([w({ unseenDone: true })]);
    expect(blockers.map((b) => b.kind)).toEqual(['unreviewed']);
  });

  it('blocks on an in-flight dispatch and on undelivered mail', () => {
    const kinds = shutdownBlockers([
      w({ name: 'alpha', dispatchOut: true }),
      w({ name: 'beta', mailboxPending: 3 }),
    ]).map((b) => b.kind);
    expect(kinds).toEqual(['dispatch', 'mailbox']);
  });

  it('sums mailbox counts rather than counting workers', () => {
    const [blocker] = shutdownBlockers([
      w({ name: 'alpha', mailboxPending: 2 }),
      w({ name: 'beta', mailboxPending: 3 }),
    ]);
    expect(blocker!.label).toContain('5 undelivered messages');
  });
});

describe('sessions', () => {
  it('splits owned from invited sessions', () => {
    const workers = [
      w({ tmuxTarget: 'myteam:1.1' }),
      w({ tmuxTarget: 'myteam:2.1' }),
      w({ tmuxTarget: 'my-own-shell:4.1', external: true }),
    ];
    expect(ownedSessions(workers)).toEqual(['myteam']);
    expect(externalSessions(workers)).toEqual(['my-own-shell']);
  });

  it('never offers to kill a session that only holds invited panes', () => {
    const workers = [w({ tmuxTarget: 'somebody-else:1.1', external: true })];
    expect(ownedSessions(workers)).toEqual([]);
    expect(teamCommands(workers, '/s/myteam').stop).toBe('');
  });
});

describe('teamCommands', () => {
  it('kills every owned session, from the live targets', () => {
    const cmds = teamCommands(
      [w({ tmuxTarget: 'myteam:1.1' }), w({ tmuxTarget: 'myteam-b:1.1' })],
      '/s/myteam'
    );
    expect(cmds.stop).toBe(
      "tmux kill-session -t 'myteam'\ntmux kill-session -t 'myteam-b'"
    );
  });

  it('addresses prune and archive by state dir', () => {
    const cmds = teamCommands([w()], '/s/myteam/feature-x');
    expect(cmds.prune).toContain("EE_STATE_DIR='/s/myteam/feature-x'");
    expect(cmds.prune).toContain('prune-workers.sh');
    expect(cmds.archive).toBe(
      `"$DARKARCHON_HOME/lib/teams.sh" archive '/s/myteam/feature-x'`
    );
  });

  it('omits prune and archive when the hub reported no state dir', () => {
    const cmds = teamCommands([w()], undefined);
    expect(cmds.prune).toBeNull();
    expect(cmds.archive).toBeNull();
    expect(cmds.full).toContain('tmux kill-session');
    expect(cmds.full).not.toContain('teams.sh');
  });

  it('orders the full block stop → prune → archive', () => {
    const { full } = teamCommands([w()], '/s/myteam');
    expect(full.indexOf('kill-session')).toBeLessThan(full.indexOf('prune-workers'));
    expect(full.indexOf('prune-workers')).toBeLessThan(full.indexOf('teams.sh'));
  });

  it('quotes paths that contain a space', () => {
    const cmds = teamCommands([w()], '/s/my team');
    expect(cmds.archive).toContain(`'/s/my team'`);
  });
});
