import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useClipboard } from './useClipboard';

describe('useClipboard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(() => Promise.resolve()) },
      configurable: true,
    });
  });
  afterEach(() => vi.useRealTimers());

  it('writes to the clipboard and flips copied to true', async () => {
    const { result } = renderHook(() => useClipboard(1000));
    expect(result.current.copied).toBe(false);
    await act(async () => {
      await result.current.copy('hello');
    });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('hello');
    expect(result.current.copied).toBe(true);
  });

  it('flips copied back to false after the feedback window', async () => {
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('x');
    });
    expect(result.current.copied).toBe(true);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.copied).toBe(false);
  });

  it('resets the timer when called again before expiry', async () => {
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('a');
    });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    await act(async () => {
      await result.current.copy('b');
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    // 400ms past second copy — still inside 500ms window → still true.
    expect(result.current.copied).toBe(true);
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.copied).toBe(false);
  });

  // A rejection means the document was not focused or permission was refused.
  // The legacy path is tried next; when that fails too the user has to be told,
  // because the alternative is finding out at the paste.
  it('reports a failure instead of swallowing it', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(() => Promise.reject(new Error('denied'))) },
      configurable: true,
    });
    document.execCommand = vi.fn(() => false);
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('x');
    });
    expect(result.current.copied).toBe(false);
    expect(result.current.failed).toBe(true);
  });

  it('falls back to execCommand when the clipboard API is absent', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    });
    document.execCommand = vi.fn(() => true);
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('over-plain-http');
    });
    expect(document.execCommand).toHaveBeenCalledWith('copy');
    expect(result.current.copied).toBe(true);
  });

  // Empty would clear the clipboard — destroying whatever the user had for a
  // command that turned out not to exist.
  it('refuses to copy an empty string', async () => {
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('');
    });
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
    expect(result.current.failed).toBe(true);
  });
});
