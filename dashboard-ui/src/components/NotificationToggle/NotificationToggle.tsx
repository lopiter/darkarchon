import { useState } from 'react';
import styles from './NotificationToggle.module.css';

type Permission = NotificationPermission | 'unsupported';

function readInitial(): Permission {
  if (typeof Notification === 'undefined') return 'unsupported';
  return Notification.permission;
}

/**
 * Lives in the dashboard top bar. Triggers Notification.requestPermission()
 * only on explicit user click — never automatically. Once denied, the
 * browser refuses further prompts; we surface that as static guidance text.
 */
export function NotificationToggle() {
  const [permission, setPermission] = useState<Permission>(readInitial);

  const onClick = async () => {
    if (permission === 'unsupported' || permission === 'denied') return;
    if (permission === 'granted') return;
    const next = await Notification.requestPermission();
    setPermission(next);
  };

  let label: string;
  let title: string;
  let cls = styles.toggle;
  switch (permission) {
    case 'granted':
      label = '🔔 ON';
      title = 'OS 알림 켜져 있음';
      cls += ` ${styles.granted}`;
      break;
    case 'denied':
      label = '🔕 OFF';
      title = '브라우저 설정에서 알림 권한을 허용해주세요';
      cls += ` ${styles.denied}`;
      break;
    case 'unsupported':
      label = '🔕 N/A';
      title = '이 브라우저는 Notification API를 지원하지 않음';
      cls += ` ${styles.denied}`;
      break;
    default:
      label = '🔔 OS 알림';
      title = '클릭하여 OS 푸시 알림 켜기';
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={cls}
      title={title}
      disabled={permission === 'unsupported' || permission === 'denied'}
    >
      {label}
    </button>
  );
}
