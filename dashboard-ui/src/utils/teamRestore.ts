/**
 * "This team's panes are gone — how do I get it back?" — the logic behind the
 * restore block of the team panel and its context-menu item.
 *
 * The mirror image of teamShutdown.ts, and read-only for the same reason: a
 * state dir only exists on the host that owns it, so the dashboard cannot run
 * anything here. It works out whether there is anything to restore and hands
 * over the one command to paste, `restore-team.sh`, addressed by state dir.
 */

import type { Worker } from '../types/domain';
import { shellQuote } from './teamShutdown';

export interface RestorePlan {
  /** Workers the hub reports dead — the ones restore-team.sh would revive. */
  dead: string[];
  /**
   * Dead workers that come back WITHOUT their conversation. An invited pane
   * never had a session id recorded for it, and only claude can resume at all;
   * restore-team.sh respawns those fresh rather than refusing, but the user
   * should know before pasting which ones start from a blank context.
   */
  fresh: string[];
  /** The command to paste, or null when the hub reported no state dir. */
  command: string | null;
  /** Same, with --dry-run: prints the per-worker plan and changes nothing. */
  dryRun: string | null;
}

export function restorePlan(
  workers: Worker[],
  stateDir: string | undefined
): RestorePlan {
  const deadWorkers = workers.filter((w) => w.state === 'dead');
  const dead = deadWorkers.map((w) => w.name).sort();
  const fresh = deadWorkers
    .filter((w) => w.external || w.process !== 'claude')
    .map((w) => w.name)
    .sort();
  const base = stateDir
    ? `EE_STATE_DIR=${shellQuote(stateDir)} "$DARKARCHON_HOME/restore-team.sh"`
    : null;
  return {
    dead,
    fresh,
    command: base,
    dryRun: base ? `${base} --dry-run` : null,
  };
}
