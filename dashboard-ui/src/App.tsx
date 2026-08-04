import { useEffect } from 'react';
import { AttentionSummary } from './components/AttentionSummary/AttentionSummary';
import { Dashboard } from './components/Dashboard/Dashboard';
import { DebugPanel } from './components/DebugPanel/DebugPanel';
import { DetailPanel } from './components/DetailPanel/DetailPanel';
import { GraphView } from './components/GraphView/GraphView';
import { ViewToggle } from './components/ViewToggle/ViewToggle';
import { useEventStream } from './hooks/useEventStream';
import { useHubPolling } from './hooks/useHubPolling';
import { useNotifications } from './hooks/useNotifications';
import { applyQuestionOverlay } from './mocks/applyQuestionOverlay';
import { dummyStatus } from './mocks/dummyStatus';
import { useDashboardStore } from './store/dashboard';

/**
 * Phase 3b — hub-connected by default. Set VITE_USE_DUMMY=1 to fall
 * back to the dummy snapshot + DebugPanel triggers for visual checks.
 */
const USE_DUMMY = import.meta.env.VITE_USE_DUMMY === '1';

// Dev-only diagnostic — inspect store from devtools console.
if (import.meta.env.DEV) {
  (window as unknown as { __darkarchon: unknown }).__darkarchon = useDashboardStore;
}

export function App() {
  const view = useDashboardStore((s) => s.view);
  useNotifications();
  // Both no-op when USE_DUMMY=1 (guarded inside each hook).
  useHubPolling();
  useEventStream();

  useEffect(() => {
    if (!USE_DUMMY) return;
    const { setRawStatus, setHosts } = useDashboardStore.getState();
    setRawStatus(dummyStatus);
    setHosts(
      applyQuestionOverlay(
        useDashboardStore.getState().hosts,
        'main',
        'frontend',
        'Which test suite should I run?'
      )
    );
  }, []);

  return (
    <>
      {USE_DUMMY && <DebugPanel />}
      {view === 'graph' ? <GraphView /> : <Dashboard />}
      <AttentionSummary />
      <ViewToggle />
      <DetailPanel />
    </>
  );
}
