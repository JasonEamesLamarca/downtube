import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { server } from '../test/server';
import { ApiError, api, setApiHooks, setCsrfToken } from './client';

describe('api client', () => {
  beforeEach(() => setCsrfToken('csrf-123'));

  it('sends X-CSRF-Token on mutating requests only', async () => {
    const seen: Record<string, string | null> = {};
    server.use(
      http.get('/api/thing', ({ request }) => { seen.GET = request.headers.get('x-csrf-token'); return HttpResponse.json({}); }),
      http.post('/api/thing', ({ request }) => { seen.POST = request.headers.get('x-csrf-token'); return HttpResponse.json({}); }),
      http.patch('/api/thing', ({ request }) => { seen.PATCH = request.headers.get('x-csrf-token'); return HttpResponse.json({}); }),
      http.delete('/api/thing', ({ request }) => { seen.DELETE = request.headers.get('x-csrf-token'); return new HttpResponse(null, { status: 204 }); }),
    );
    await api('/thing');
    await api('/thing', { method: 'POST', body: { a: 1 } });
    await api('/thing', { method: 'PATCH', body: {} });
    await api('/thing', { method: 'DELETE' });
    expect(seen).toEqual({ GET: null, POST: 'csrf-123', PATCH: 'csrf-123', DELETE: 'csrf-123' });
  });

  it('maps JSON error bodies to ApiError', async () => {
    server.use(http.get('/api/x', () =>
      HttpResponse.json({ error: { code: 'confirmation_required', message: 'Large', details: { video_count: 5 } } }, { status: 409 })));
    const err = await api('/x').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: 'confirmation_required', message: 'Large', details: { video_count: 5 } });
  });

  it('calls hooks on 401 and acknowledgement_required', async () => {
    const onUnauthorized = vi.fn();
    const onAcknowledgementRequired = vi.fn();
    setApiHooks({ onUnauthorized, onAcknowledgementRequired });
    server.use(
      http.get('/api/a', () => HttpResponse.json({ error: { code: 'unauthenticated', message: 'no' } }, { status: 401 })),
      http.post('/api/b', () => HttpResponse.json({ error: { code: 'acknowledgement_required', message: 'ack' } }, { status: 403 })),
    );
    await expect(api('/a')).rejects.toBeInstanceOf(ApiError);
    await expect(api('/b', { method: 'POST' })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(onAcknowledgementRequired).toHaveBeenCalledTimes(1);
  });

  it('falls back to a generic error for non-JSON failures', async () => {
    server.use(http.get('/api/y', () => new HttpResponse('boom', { status: 500 })));
    const err = await api('/y', { noAuthRedirect: true }).catch((e) => e);
    expect(err).toMatchObject({ status: 500, code: 'http_error' });
  });
});
