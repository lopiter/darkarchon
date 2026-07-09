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
      title = 'OS notifications are on';
      cls += ` ${styles.granted}`;
      break;
    case 'denied':
      label = '🔕 OFF';
      title = 'Allow notification permission in your browser settings';
      cls += ` ${styles.denied}`;
      break;
    case 'unsupported':
      label = '🔕 N/A';
      title = 'This browser does not support the Notification API';
      cls += ` ${styles.denied}`;
      break;
    default:
      label = '🔔 Notify';
      title = 'Click to enable OS push notifications';
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
