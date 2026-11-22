import type { ErrorBody } from './types';

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(status: number, body: ErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.details = body.details;
  }
}

type Hooks = {
  onUnauthorized: () => void;
  onAcknowledgementRequired: () => void;
};

let csrfToken: string | null = null;
let hooks: Hooks = {
  onUnauthorized: () => {
    if (!window.location.pathname.startsWith('/login')) window.location.assign('/login');
  },
  onAcknowledgementRequired: () => {},
};

export function setCsrfToken(t: string | null): void { csrfToken = t; }
export function getCsrfToken(): string | null { return csrfToken; }
export function setApiHooks(h: Partial<Hooks>): void { hooks = { ...hooks, ...h }; }

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  /** Do not trigger the global 401 redirect (used for the initial /auth/me probe). */
  noAuthRedirect?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions['query']): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  }
  const qs = params.toString();
  return `/api${path}${qs ? `?${qs}` : ''}`;
}

async function parseError(res: Response): Promise<ErrorBody> {
  try {
    const j = await res.json();
    if (j?.error?.code) return j.error as ErrorBody;
    if (j?.detail) return { code: 'http_error', message: typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail), details: j.detail };
  } catch { /* fall through */ }
  return { code: 'http_error', message: res.statusText || `HTTP ${res.status}` };
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (MUTATING.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const res = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    credentials: 'same-origin',
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  const err = new ApiError(res.status, await parseError(res));
  if (res.status === 401 && !opts.noAuthRedirect) hooks.onUnauthorized();
  if (res.status === 403 && err.code === 'acknowledgement_required') hooks.onAcknowledgementRequired();
  throw err;
}

/** Upload a file as the raw request body (XMLHttpRequest, for upload progress). */
export function uploadFile<T>(path: string, file: Blob, opts: { query?: RequestOptions['query']; onProgress?: (pct: number) => void; signal?: AbortSignal } = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', buildUrl(path, opts.query));
    xhr.withCredentials = true;
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) opts.onProgress?.((e.loaded / e.total) * 100); };
    xhr.onload = () => {
      let body: unknown = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body as T); return; }
      const err = (body as { error?: ErrorBody } | null)?.error ?? { code: 'http_error', message: xhr.statusText || `HTTP ${xhr.status}` };
      if (xhr.status === 401) hooks.onUnauthorized();
      reject(new ApiError(xhr.status, err));
    };
    xhr.onerror = () => reject(new ApiError(0, { code: 'network', message: 'The upload failed (network error).' }));
    xhr.onabort = () => reject(new ApiError(0, { code: 'aborted', message: 'Upload cancelled.' }));
    opts.signal?.addEventListener('abort', () => xhr.abort());
    xhr.send(file);
  });
}
