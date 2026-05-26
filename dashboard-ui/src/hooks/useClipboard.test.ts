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

  it('swallows clipboard errors silently', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(() => Promise.reject(new Error('denied'))) },
      configurable: true,
    });
    const { result } = renderHook(() => useClipboard(500));
    await act(async () => {
      await result.current.copy('x');
    });
    expect(result.current.copied).toBe(false);
  });
});
