import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Copy text to the OS clipboard with a transient `copied` flag for UI
 * feedback. The flag flips back to false after `feedbackMs` (default 1s).
 */
export function useClipboard(feedbackMs = 1000): {
  copy: (text: string) => Promise<void>;
  copied: boolean;
} {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(() => setCopied(false), feedbackMs);
      } catch {
        // Surface failure silently — Phase 4 can add a static "copy failed" hint.
      }
    },
    [feedbackMs]
  );

  useEffect(
    () => () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    },
    []
  );

  return { copy, copied };
}
