/**
 * Arrow-key focus traversal across worker cards while the detail panel
 * is closed. Mirrors useArrowKeyNav (which handles the open state by
 * advancing the selected worker) — this one moves DOM focus instead so
 * the user can highlight a card and press Enter/Space to open it.
 *
 * DESIGN.md Section 6.1 — "Keyboard navigation — arrow keys move between
 * cards, Enter opens the panel".
 */

import { useEffect } from 'react';

const CARD_SELECTOR = '[data-card-wrapper="1"]';

export function useCardKeyboardNav(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      if (
        e.key !== 'ArrowUp' &&
        e.key !== 'ArrowDown' &&
        e.key !== 'ArrowLeft' &&
        e.key !== 'ArrowRight'
      ) {
        return;
      }
      // Don't steal arrows from form fields (none today, defensive).
      const tgt = e.target;
      if (
        tgt instanceof HTMLInputElement ||
        tgt instanceof HTMLTextAreaElement ||
        (tgt instanceof HTMLElement && tgt.isContentEditable)
      ) {
        return;
      }
      const cards = Array.from(
        document.querySelectorAll<HTMLElement>(CARD_SELECTOR)
      );
      if (cards.length === 0) return;
      const focused = document.activeElement;
      const cur =
        focused instanceof HTMLElement ? cards.indexOf(focused) : -1;
      const dir = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
      const next =
        cur === -1
          ? dir > 0
            ? 0
            : cards.length - 1
          : (cur + dir + cards.length) % cards.length;
      e.preventDefault();
      cards[next]!.focus();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [active]);
}
