import styles from './TeamSection.module.css';

interface Props {
  name: string;
}

export function TeamLabel({ name }: Props) {
  return <div className={styles.label}>{name}</div>;
}
