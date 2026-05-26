import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useEscapeKey } from './useEscapeKey';

function dispatchKey(key: string) {
  document.dispatchEvent(new KeyboardEvent('keydown', { key }));
}

describe('useEscapeKey', () => {
  it('fires onEscape only for the Escape key while active', () => {
    const onEscape = vi.fn();
    renderHook(() => useEscapeKey(true, onEscape));
    dispatchKey('a');
    expect(onEscape).not.toHaveBeenCalled();
    dispatchKey('Escape');
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it('does not fire when inactive', () => {
    const onEscape = vi.fn();
    renderHook(() => useEscapeKey(false, onEscape));
    dispatchKey('Escape');
    expect(onEscape).not.toHaveBeenCalled();
  });

  it('removes the listener when unmounting', () => {
    const onEscape = vi.fn();
    const { unmount } = renderHook(() => useEscapeKey(true, onEscape));
    unmount();
    dispatchKey('Escape');
    expect(onEscape).not.toHaveBeenCalled();
  });
});
