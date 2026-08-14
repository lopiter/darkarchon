import { describe, expect, it } from 'vitest';
import type { Worker } from '../../types/domain';
import { workerMenuItems } from './menus';

function worker(overrides: Partial<Worker> = {}): Worker {
  return {
    id: 'host:dark:1.1',
    name: 'darkarchon',
    state: 'idle',
    role: '',
    tmuxTarget: 'dark:1.1',
    process: 'claude',
    external: false,
    isOrchestrator: false,
    enteredStateAt: '2026-08-14T00:00:00Z',
    unseenDone: false,
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

describe('workerMenuItems — Copy session name', () => {
  it('offers the peer name for copy when the pane hosts a messaging session', () => {
    const item = workerMenuItems(worker({ peerName: 'darkarchon-c3' })).find(
      (i) => i.label === 'Copy session name'
    );
    expect(item).toBeDefined();
    expect(item!.copy).toBe('darkarchon-c3');
    expect(item!.hint).toBe('darkarchon-c3');
    expect(item!.disabled).toBe(false);
  });

  it('disables the item when no messaging session is known', () => {
    const item = workerMenuItems(worker()).find(
      (i) => i.label === 'Copy session name'
    );
    expect(item).toBeDefined();
    expect(item!.disabled).toBe(true);
    expect(item!.copy).toBe('');
  });
});
