/**
 * Format a duration in milliseconds as a short label: '3s', '2m', '1h', '5d'.
 * Used in host header ('last ping 3s') and elsewhere.
 */
export function formatPing(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}
