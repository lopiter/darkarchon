/**
 * DESIGN.md Section 8 — three empty-state variants.
 *
 *   variant="no-workers"   no workers anywhere (Section 8.1, first-run)
 *   variant="all-stale"    every host stale (Section 8.3, warning tone)
 *
 * Per-host "host present but no workers" (Section 8.2) is rendered
 * inline by HostGroup so the host header stays visible.
 */

import styles from './EmptyState.module.css';

interface Props {
  variant: 'no-workers' | 'all-stale';
  hostCount?: number;
}

export function EmptyState({ variant, hostCount = 0 }: Props) {
  if (variant === 'all-stale') {
    return (
      <div className={`${styles.wrap} ${styles.warn}`}>
        <div className={styles.card}>
          <div className={styles.headline}>
            모든 호스트가 응답하지 않습니다
          </div>
          <div className={styles.body}>
            agent가 실행 중인지 확인하세요. 마지막 ping 이후 15초가 지나면
            stale로 표시됩니다.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.headline}>아직 발견된 워커가 없습니다</div>
        <div className={styles.body}>
          darkarchon은 tmux 세션에서 클로드가 떠 있는 것을 자동으로 찾습니다.
          어떤 PC에서든 클로드 코드를 평소처럼 시작하면 여기에 나타납니다.
        </div>
        <div className={styles.meta}>
          <span>연결된 호스트: {hostCount}</span>
        </div>
      </div>
    </div>
  );
}
