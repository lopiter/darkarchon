import { describe, expect, it } from 'vitest';
import {
  agentChipPaint,
  CHIP_PAD_R,
  CHIP_SIZE,
  CHIP_SUBTITLE_GAP,
  clipSubtitle,
  SUBTITLE_MAX_CHARS,
  SUBTITLE_X,
  subtitlePixelBudget,
} from './renderer';

describe('agentChipPaint', () => {
  it('returns C / X / G paint for known processes', () => {
    expect(agentChipPaint('claude')).toEqual({
      letter: 'C',
      fill: '#e6c3a0',
      bg: 'rgba(212,165,122,0.16)',
    });
    expect(agentChipPaint('codex')).toEqual({
      letter: 'X',
      fill: '#8a97ab',
      bg: 'rgba(148,163,184,0.16)',
    });
    expect(agentChipPaint('grok')).toEqual({
      letter: 'G',
      fill: '#a5b4fc',
      bg: 'rgba(129,140,248,0.16)',
    });
  });

  it('returns null for unknown process so the renderer draws nothing', () => {
    expect(agentChipPaint('hermes')).toBeNull();
    expect(agentChipPaint('')).toBeNull();
    expect(agentChipPaint(undefined)).toBeNull();
  });
});

/** Monospace 10px stand-in (~0.6em). Real canvas widths are checked in-browser. */
const ADVANCE = 6;
const measure = (s: string) => s.length * ADVANCE;

const NODE_W = 224;
const CHIP_LEFT = NODE_W - CHIP_PAD_R - CHIP_SIZE;

describe('subtitlePixelBudget', () => {
  it('is null when there is no chip so the 30-char clip stands alone', () => {
    expect(subtitlePixelBudget(NODE_W, false)).toBeNull();
  });

  it('stops at the chip left edge minus a gap', () => {
    expect(subtitlePixelBudget(NODE_W, true)).toBe(
      CHIP_LEFT - CHIP_SUBTITLE_GAP - SUBTITLE_X
    );
  });
});

describe('clipSubtitle', () => {
  it('keeps the 30-char clip when there is no chip, even for a long tail', () => {
    const long = 'erp-api-backend-batch · awaiting user';
    expect(long.length).toBeGreaterThan(SUBTITLE_MAX_CHARS);
    const out = clipSubtitle(long, SUBTITLE_MAX_CHARS, null, measure);
    expect(out.length).toBe(SUBTITLE_MAX_CHARS);
    expect(out.endsWith('…')).toBe(true);
    // pixel-unbounded: the char-clipped string is allowed to reach past the chip
    expect(SUBTITLE_X + measure(out)).toBeGreaterThan(CHIP_LEFT);
  });

  it('does not let a chipped worker subtitle cross the chip left edge', () => {
    const long = 'homepage-backend · rate limited';
    const maxW = subtitlePixelBudget(NODE_W, true);
    const out = clipSubtitle(long, SUBTITLE_MAX_CHARS, maxW, measure);
    expect(maxW).not.toBeNull();
    expect(measure(out)).toBeLessThanOrEqual(maxW!);
    expect(SUBTITLE_X + measure(out)).toBeLessThanOrEqual(CHIP_LEFT - CHIP_SUBTITLE_GAP);
    expect(out.endsWith('…')).toBe(true);
  });

  it('applies the same pixel cap to busy-state dynamic subtitles', () => {
    const busy = '✽ erp-api-backend-batch still compacting a huge context (42s)';
    const maxW = subtitlePixelBudget(NODE_W, true);
    const out = clipSubtitle(busy, SUBTITLE_MAX_CHARS, maxW, measure);
    expect(measure(out)).toBeLessThanOrEqual(maxW!);
    expect(SUBTITLE_X + measure(out)).toBeLessThanOrEqual(CHIP_LEFT);
  });

  it('leaves a short subtitle intact when it already fits', () => {
    const maxW = subtitlePixelBudget(NODE_W, true);
    expect(clipSubtitle('idle · 3m', SUBTITLE_MAX_CHARS, maxW, measure)).toBe('idle · 3m');
  });
});
