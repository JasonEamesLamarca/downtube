import { ApiError } from '../api/client';

export function fieldErrors(e: unknown): Record<string, string> {
  const res: Record<string, string> = {};
  if (!(e instanceof ApiError) || e.status !== 422) return res;
  const d = e.details as unknown;
  if (Array.isArray(d)) {
    for (const it of d) {
      const loc = (it as { loc?: unknown[] }).loc;
      const field = Array.isArray(loc) ? String(loc[loc.length - 1]) : '';
      if (field) res[field] = String((it as { msg?: string }).msg ?? 'Invalid value');
    }
  } else if (d && typeof d === 'object') {
    for (const [k, v] of Object.entries(d as Record<string, unknown>)) res[k] = Array.isArray(v) ? v.join(', ') : String(v);
  }
  return res;
}
