import styles from './HostGroup.module.css';

interface Props {
  count: number;
  expanded: boolean;
  onToggle: () => void;
}

export function HiddenToggle({ count, expanded, onToggle }: Props) {
  if (count <= 0) return null;
  return (
    <button
      type="button"
      className={styles.hiddenToggle}
      onClick={onToggle}
      aria-pressed={expanded}
    >
      {expanded ? `hide dead (${count})` : `${count} hidden`}
    </button>
  );
}
