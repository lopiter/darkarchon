/**
 * FLIP (First / Last / Invert / Play) layout transition for a list of
 * children identified by `keys`.
 *
 * DESIGN.md Section 7.2: 카드 위치 재정렬 0.6s.
 *
 * Limitations (acceptable for the dashboard's scale):
 *   - reads bounding rects on every render where keys change → O(n) per
 *     team, n is ~10 cards max. fine.
 *   - children must be in the same order as `keys`. TeamSection sorts
 *     workers and maps them in that order, so direct correspondence holds.
 *   - skipped entirely when prefers-reduced-motion: reduce.
 */

import { useLayoutEffect, useRef, type RefObject } from 'react';

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useFlipAnimation(
  containerRef: RefObject<HTMLElement | null>,
  keys: string[],
  durationMs = 600
): void {
  const prevRectsRef = useRef<Map<string, DOMRect>>(new Map());
  const keysSignature = keys.join('|');

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (prefersReducedMotion()) {
      prevRectsRef.current = new Map();
      return;
    }

    const children = Array.from(container.children) as HTMLElement[];
    const newRects = new Map<string, DOMRect>();
    keys.forEach((key, i) => {
      const child = children[i];
      if (!child) return;
      newRects.set(key, child.getBoundingClientRect());
    });

    keys.forEach((key, i) => {
      const child = children[i];
      if (!child) return;
      const prev = prevRectsRef.current.get(key);
      const next = newRects.get(key);
      if (!prev || !next) return;
      const dx = prev.left - next.left;
      const dy = prev.top - next.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;

      child.style.transition = 'none';
      child.style.transform = `translate(${dx}px, ${dy}px)`;
      // Force reflow so the browser commits the inverse position before
      // we transition back to identity.
      void child.getBoundingClientRect();
      child.style.transition = `transform ${durationMs}ms ease-out`;
      child.style.transform = '';
    });

    prevRectsRef.current = newRects;
  }, [keysSignature, containerRef, durationMs, keys]);
}
