/**
 * Right-click menu — one at a time, positioned at the cursor.
 *
 * Kept as a single store-free module rather than per-component state so that
 * opening a menu anywhere closes the one already open, and so the menu can
 * escape its row's `overflow` box by rendering at the top of the app.
 *
 * Items only ever copy or open a panel. Nothing here executes anything on a
 * host — see `teamShutdown.ts` for why shutdown stays a paste.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { copyText } from '../../utils/copyText';
import styles from './ContextMenu.module.css';

export interface MenuItem {
  label: string;
  /**
   * Text this item puts on the clipboard. The menu performs the copy itself
   * and reports the outcome in place — an item that copies must never look the
   * same whether it worked or not, which is what a bare onSelect gave us.
   */
  copy?: string;
  /** Anything that is not a copy. Runs as the menu closes. */
  onSelect?: () => void;
  /** Right-aligned hint — the command a copy item puts on the clipboard. */
  hint?: string;
  /** Renders in red. For the items that end something. */
  danger?: boolean;
  disabled?: boolean;
}

export interface MenuRequest {
  x: number;
  y: number;
  title: string;
  items: MenuItem[];
}

/**
 * Opening the menu goes through a DOM event on `window` rather than a
 * module-level callback, and that is not incidental.
 *
 * The obvious version — `let listener` up here, the component assigning to it
 * on mount — silently breaks under HMR, which this app always runs with
 * (`dashboard-ui.sh start` is `npm run dev`). Editing any UI file makes Vite
 * re-evaluate this module; the caller then imports a *fresh* copy whose
 * `listener` is still null while the mounted menu is registered on the old
 * one. Right-click stops doing anything at all, with no error, until someone
 * reloads — and "the menu just doesn't open" is very hard to tell apart from
 * "the feature is broken".
 *
 * `window` is the one registry every copy of this module agrees on, so the two
 * halves cannot drift apart no matter how the module graph is rebuilt.
 */
const OPEN_EVENT = 'darkarchon:context-menu';

/** Open the context menu. Call from an `onContextMenu` handler. */
export function openContextMenu(req: MenuRequest): void {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: req }));
}

export function closeContextMenu(): void {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: null }));
}

/** Outcome of the copy the user just asked for, held until the menu closes. */
interface CopyStatus {
  label: string;
  state: 'copied' | 'failed';
  text: string;
}

/** How long a successful copy stays on screen before the menu closes itself.
 *  A failure does not auto-close — the fallback box is the only way left to
 *  get the command, so it waits to be dismissed. */
const COPIED_CLOSE_MS = 700;

/** Mount once, near the app root. */
export function ContextMenu() {
  const [req, setReq] = useState<MenuRequest | null>(null);
  const [status, setStatus] = useState<CopyStatus | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const next = (e as CustomEvent<MenuRequest | null>).detail;
      setStatus(null);
      setReq(next);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  const onSelect = (item: MenuItem) => {
    if (item.copy === undefined) {
      setReq(null);
      item.onSelect?.();
      return;
    }
    void copyText(item.copy).then((ok) => {
      setStatus({
        label: item.label,
        state: ok ? 'copied' : 'failed',
        text: item.copy!,
      });
      if (ok) setTimeout(() => setReq(null), COPIED_CLOSE_MS);
    });
  };

  // Flip the menu back inside the viewport before it paints — a right-click
  // near the bottom edge is the common case, not the exception.
  useLayoutEffect(() => {
    if (!req) {
      setPos(null);
      return;
    }
    const el = ref.current;
    const w = el?.offsetWidth ?? 220;
    const h = el?.offsetHeight ?? 0;
    setPos({
      left: Math.max(4, Math.min(req.x, window.innerWidth - w - 4)),
      top: Math.max(4, Math.min(req.y, window.innerHeight - h - 4)),
    });
  }, [req]);

  useEffect(() => {
    if (!req) return;
    const close = () => setReq(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    // `capture` so a press that also lands on a card closes the menu rather
    // than leaving it floating over the newly-opened panel — but never for a
    // press on the menu itself, which would unmount the item before its own
    // click could fire.
    const onDown = (e: MouseEvent) => {
      if (e.target instanceof Node && ref.current?.contains(e.target)) return;
      close();
    };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [req]);

  if (!req) return null;

  return (
    <div
      ref={ref}
      className={styles.menu}
      role="menu"
      aria-label={req.title}
      style={{
        left: pos?.left ?? req.x,
        top: pos?.top ?? req.y,
        // Hidden for the one frame between mount and measurement.
        visibility: pos ? 'visible' : 'hidden',
      }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className={styles.title}>{req.title}</div>
      {req.items.map((item) => {
        const state = status?.label === item.label ? status.state : null;
        return (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={`${styles.item} ${item.danger ? styles.danger : ''}`}
            disabled={item.disabled}
            onClick={() => onSelect(item)}
          >
            <span>{item.label}</span>
            {state === 'copied' && <span className={styles.ok}>✓ copied</span>}
            {state === 'failed' && (
              <span className={styles.bad}>couldn't copy — select it below</span>
            )}
            {!state && item.hint && (
              <span className={styles.hint}>{item.hint}</span>
            )}
          </button>
        );
      })}
      {/* Shown only when the copy failed: the command still has to reach the
          user somehow, and a selectable box is the one path that needs no
          permission at all. */}
      {status?.state === 'failed' && status.text && (
        <textarea
          className={styles.fallbackBox}
          readOnly
          value={status.text}
          ref={(el) => el?.select()}
          onClick={(e) => e.currentTarget.select()}
        />
      )}
    </div>
  );
}
