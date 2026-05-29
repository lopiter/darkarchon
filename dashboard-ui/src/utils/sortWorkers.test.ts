import { describe, expect, it } from 'vitest';
import type { Worker, WorkerState } from '../types/domain';
import { sortWorkersForTeam } from './sortWorkers';

function w(
  name: string,
  state: WorkerState,
  enteredStateAt = '2026-05-23T15:00:00Z'
): Worker {
  return {
    id: name,
    name,
    state,
    role: '',
    tmuxTarget: `${name}:1`,
    process: 'claude',
    isOrchestrator: false,
    enteredStateAt,
    dispatchOut: false,
    dispatchIn: false,
    mailboxPending: 0,
    incomingDispatches: [],
    outgoingDispatches: [],
    mailboxSenders: [],
    recentTasks: [],
  };
}

describe('sortWorkersForTeam', () => {
  it('places awaiting_user:* at the top, enteredStateAt desc', () => {
    const sorted = sortWorkersForTeam(
      [
        w('a', 'idle'),
        w('b', 'awaiting_user:typed', '2026-05-23T15:00:00Z'),
        w('c', 'awaiting_user:question', '2026-05-23T15:00:30Z'),
      ],
      false
    );
    expect(sorted.map((x) => x.name)).toEqual(['c', 'b', 'a']);
  });

  it('rate_limited above busy/compacting', () => {
    const sorted = sortWorkersForTeam(
      [w('a', 'busy'), w('b', 'rate_limited'), w('c', 'compacting')],
      false
    );
    expect(sorted.map((x) => x.name)).toEqual(['b', 'a', 'c']);
  });

  it('busy and compacting sorted by name', () => {
    const sorted = sortWorkersForTeam(
      [w('zebra', 'busy'), w('alpha', 'compacting')],
      false
    );
    expect(sorted.map((x) => x.name)).toEqual(['alpha', 'zebra']);
  });

  it('idle below busy/compacting', () => {
    const sorted = sortWorkersForTeam(
      [w('a', 'idle'), w('b', 'busy')],
      false
    );
    expect(sorted.map((x) => x.name)).toEqual(['b', 'a']);
  });

  it('excludes dead/unknown when showDead=false', () => {
    const sorted = sortWorkersForTeam(
      [w('a', 'idle'), w('b', 'dead'), w('c', 'unknown')],
      false
    );
    expect(sorted.map((x) => x.name)).toEqual(['a']);
  });

  it('includes dead/unknown at bottom when showDead=true', () => {
    const sorted = sortWorkersForTeam(
      [w('a', 'dead'), w('b', 'idle'), w('c', 'unknown')],
      true
    );
    expect(sorted.map((x) => x.name)).toEqual(['b', 'a', 'c']);
  });

  it('empty input → empty output', () => {
    expect(sortWorkersForTeam([], false)).toEqual([]);
    expect(sortWorkersForTeam([], true)).toEqual([]);
  });

  it('groups awaiting_user:typed and awaiting_user:question together', () => {
    const sorted = sortWorkersForTeam(
      [
        w('rate', 'rate_limited'),
        w('typed-old', 'awaiting_user:typed', '2026-05-23T14:00:00Z'),
        w('question-new', 'awaiting_user:question', '2026-05-23T15:00:00Z'),
      ],
      false
    );
    // both awaiting first (question-new most recent), then rate_limited
    expect(sorted.map((x) => x.name)).toEqual([
      'question-new',
      'typed-old',
      'rate',
    ]);
  });
});
