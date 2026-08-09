/**
 * The two right-click menus, built from a worker or a team.
 *
 * Lives apart from ContextMenu so the row components stay presentational and
 * so the item lists can be asserted in tests without rendering a menu.
 */

import type { MouseEvent } from 'react';
import { useDashboardStore } from '../../store/dashboard';
import type { Team, Worker } from '../../types/domain';
import { shutdownBlockers, teamCommands } from '../../utils/teamShutdown';
import { openContextMenu, type MenuItem } from './ContextMenu';

function copy(text: string): void {
  navigator.clipboard?.writeText(text).catch(() => {});
}

export function workerMenuItems(worker: Worker): MenuItem[] {
  const attach = `tmux attach -t '${worker.tmuxTarget}'`;
  return [
    {
      label: 'Open detail panel',
      onSelect: () => useDashboardStore.getState().selectWorker(worker.id),
    },
    {
      label: 'Copy tmux target',
      hint: worker.tmuxTarget,
      onSelect: () => copy(worker.tmuxTarget),
    },
    { label: 'Copy attach command', hint: attach, onSelect: () => copy(attach) },
    {
      label: 'Copy kill-worker command',
      // Invited panes belong to someone else's session and kill-worker.sh
      // refuses them outright — offering the command would only produce an
      // error the user has to go read.
      hint: worker.external ? 'invited pane — refused' : `kill-worker.sh ${worker.name}`,
      danger: true,
      disabled: worker.external,
      onSelect: () =>
        copy(`"$DARKARCHON_HOME/lib/kill-worker.sh" '${worker.name}'`),
    },
  ];
}

export function teamMenuItems(host: string, team: Team): MenuItem[] {
  const cmds = teamCommands(team.workers, team.stateDir);
  const blockers = shutdownBlockers(team.workers);
  const busy = blockers.length > 0;

  return [
    {
      label: 'Open team panel',
      onSelect: () => useDashboardStore.getState().selectTeam(host, team.name),
    },
    {
      label: 'Copy stop-session command',
      hint: busy ? `${blockers[0]!.label} — open panel` : cmds.stop || 'no owned session',
      danger: true,
      disabled: !cmds.stop,
      onSelect: () => copy(cmds.stop),
    },
    {
      label: 'Copy full wind-down',
      hint: 'stop → prune → archive',
      danger: true,
      onSelect: () => copy(cmds.full),
    },
    {
      label: 'Copy archive command',
      hint: cmds.archive ? 'teams.sh archive' : 'no state dir',
      disabled: !cmds.archive,
      onSelect: () => copy(cmds.archive ?? ''),
    },
  ];
}

/** `onContextMenu` handler for a worker row. */
export function onWorkerContextMenu(worker: Worker) {
  return (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openContextMenu({
      x: e.clientX,
      y: e.clientY,
      title: worker.name,
      items: workerMenuItems(worker),
    });
  };
}

/** `onContextMenu` handler for a team label. */
export function onTeamContextMenu(host: string, team: Team) {
  return (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openContextMenu({
      x: e.clientX,
      y: e.clientY,
      title: `${team.name} · ${host}`,
      items: teamMenuItems(host, team),
    });
  };
}
