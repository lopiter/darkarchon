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
import styles from './ContextMenu.module.css';

export interface MenuItem {
  label: string;
  onSelect: () => void;
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

type Listener = (req: MenuRequest | null) => void;
let listener: Listener | null = null;

/** Open the context menu. Call from an `onContextMenu` handler. */
export function openContextMenu(req: MenuRequest): void {
  listener?.(req);
}

export function closeContextMenu(): void {
  listener?.(null);
}

/** Mount once, near the app root. */
export function ContextMenu() {
  const [req, setReq] = useState<MenuRequest | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    listener = setReq;
    return () => {
      listener = null;
    };
  }, []);

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
      {req.items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className={`${styles.item} ${item.danger ? styles.danger : ''}`}
          disabled={item.disabled}
          onClick={() => {
            setReq(null);
            item.onSelect();
          }}
        >
          <span>{item.label}</span>
          {item.hint && <span className={styles.hint}>{item.hint}</span>}
        </button>
      ))}
    </div>
  );
}
