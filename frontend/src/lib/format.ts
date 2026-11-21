import type { ResolutionCap } from '../api/types';

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${Math.round(n)} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  if (bps === null || bps === undefined || bps <= 0) return '—';
  return `${formatBytes(bps)}/s`;
}

export function formatDuration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '—';
  const t = Math.round(s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const pad = (x: number) => String(x).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export function formatEta(s: number | null | undefined): string {
  if (s === null || s === undefined || s < 0) return '—';
  return formatDuration(s);
}

export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const diff = Math.round((t - now) / 1000);
  const abs = Math.abs(diff);
  const units: [number, string][] = [[86400 * 365, 'year'], [86400 * 30, 'month'], [86400, 'day'], [3600, 'hour'], [60, 'minute']];
  if (abs < 45) return diff >= 0 ? 'in a few seconds' : 'just now';
  for (const [secs, name] of units) {
    if (abs >= secs) {
      const n = Math.floor(abs / secs);
      const label = `${n} ${name}${n === 1 ? '' : 's'}`;
      return diff >= 0 ? `in ${label}` : `${label} ago`;
    }
  }
  return diff >= 0 ? 'in 1 minute' : '1 minute ago';
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString();
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

export const RESOLUTION_OPTIONS: { value: ResolutionCap; label: string }[] = [
  { value: null, label: 'Best available (≤4K)' },
  { value: 2160, label: '2160p 4K UHD' },
  { value: 1440, label: '1440p' },
  { value: 1080, label: '1080p Full HD' },
  { value: 720, label: '720p HD' },
  { value: 480, label: '480p SD' },
  { value: 360, label: '360p Low' },
];

export function resolutionLabel(v: number | null | undefined): string {
  if (v === null || v === undefined) return RESOLUTION_OPTIONS[0].label;
  return RESOLUTION_OPTIONS.find((o) => o.value === v)?.label ?? `${v}p`;
}

/** Short label for an actual video height, e.g. 2160 -> "2160p". */
export function heightLabel(h: number | null | undefined): string {
  return h ? `${h}p` : '—';
}

export function parseResolution(s: string): ResolutionCap {
  return s === '' ? null : (Number(s) as ResolutionCap);
}

export const RESOLUTION_NOTE =
  'Resolutions are maximum limits. DownTube always keeps the best ORIGINAL quality up to your limit (max 4K) and never upscales or re-encodes video.';

const ID_RE = /^[A-Za-z0-9_-]{11}$/;

/** Extract an 11-char YouTube video ID from a bare ID or common URL shapes. */
export function extractVideoId(input: string): string | null {
  const s = input.trim();
  if (!s) return null;
  if (ID_RE.test(s)) return s;
  let url: URL;
  try {
    url = new URL(/^[a-z]+:\/\//i.test(s) ? s : `https://${s}`);
  } catch { return null; }
  const host = url.hostname.replace(/^(www\.|m\.|music\.)/, '');
  if (host === 'youtu.be') {
    const id = url.pathname.split('/')[1] ?? '';
    return ID_RE.test(id) ? id : null;
  }
  if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    const v = url.searchParams.get('v');
    if (v && ID_RE.test(v)) return v;
    const m = url.pathname.match(/^\/(?:shorts|embed|live|v)\/([A-Za-z0-9_-]{11})(?:[/?]|$)/);
    if (m) return m[1];
  }
  return null;
}
