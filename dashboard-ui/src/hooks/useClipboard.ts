import { useCallback, useEffect, useRef, useState } from 'react';
import { copyText } from '../utils/copyText';

/**
 * Copy text to the OS clipboard with a transient status for UI feedback.
 * `copied` and `failed` both clear after `feedbackMs` (default 1s).
 *
 * A failure is reported rather than swallowed: a copy button that does
 * nothing, silently, is indistinguishable from one that worked, and the user
 * only finds out at the paste.
 */
export function useClipboard(feedbackMs = 1000): {
  copy: (text: string) => Promise<void>;
  copied: boolean;
  failed: boolean;
} {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(
    async (text: string) => {
      const ok = await copyText(text);
      setStatus(ok ? 'copied' : 'failed');
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setStatus('idle'), feedbackMs);
    },
    [feedbackMs]
  );

  useEffect(
    () => () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    },
    []
  );

  return { copy, copied: status === 'copied', failed: status === 'failed' };
}
