import { describe, expect, it } from 'vitest';
import { extractVideoId, formatBytes, formatDuration, formatRelative, formatSpeed, resolutionLabel } from './format';

describe('resolutionLabel', () => {
  it('labels caps', () => {
    expect(resolutionLabel(null)).toBe('Best available (≤4K)');
    expect(resolutionLabel(2160)).toBe('2160p 4K UHD');
    expect(resolutionLabel(1080)).toBe('1080p Full HD');
    expect(resolutionLabel(360)).toBe('360p Low');
    expect(resolutionLabel(999)).toBe('999p');
  });
});

describe('extractVideoId', () => {
  const id = 'dQw4w9WgXcQ';
  it.each([
    id,
    `  ${id} `,
    `https://www.youtube.com/watch?v=${id}`,
    `https://www.youtube.com/watch?v=${id}&t=30s&list=PL1`,
    `https://youtu.be/${id}?si=abc`,
    `https://m.youtube.com/shorts/${id}`,
    `youtube.com/embed/${id}`,
    `https://www.youtube.com/live/${id}`,
  ])('extracts from %s', (input) => expect(extractVideoId(input)).toBe(id));
  it.each(['', 'short', 'https://example.com/watch?v=dQw4w9WgXcQ', 'https://www.youtube.com/watch?v=tooshort', 'not a url at all!!'])(
    'rejects %j', (input) => expect(extractVideoId(input)).toBeNull());
});

describe('formatters', () => {
  it('formats bytes, speed, duration', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB');
    expect(formatBytes(null)).toBe('—');
    expect(formatSpeed(2 * 1024 * 1024)).toBe('2.0 MB/s');
    expect(formatDuration(3725)).toBe('1:02:05');
    expect(formatDuration(65)).toBe('1:05');
  });
  it('formats relative times', () => {
    const now = Date.parse('2026-01-10T12:00:00Z');
    expect(formatRelative('2026-01-10T10:00:00Z', now)).toBe('2 hours ago');
    expect(formatRelative('2026-01-10T12:05:00Z', now)).toBe('in 5 minutes');
    expect(formatRelative(null)).toBe('never');
  });
});
