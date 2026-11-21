import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { renderPage } from '../test/utils';
import { job } from '../test/fixtures';
import DownloadsPage from './Downloads';

const counts = { queued: 2, running: 1, paused: 0, completed: 9, failed: 1, cancelled: 0, unsupported: 0, skipped: 0 };

describe('DownloadsPage', () => {
  it('shows counts, progress and posts job actions', async () => {
    const calls: string[] = [];
    server.use(
      http.get('/api/downloads/counts', () => HttpResponse.json(counts)),
      http.get('/api/downloads', ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        const items = status === 'running' ? [job()] : status === 'failed' ? [job({ id: 7, status: 'failed', title: 'Broken', error_message: 'network down', error_code: 'network' })] : [];
        return HttpResponse.json({ items, total: items.length, page: 1, page_size: 25 });
      }),
      http.post('/api/downloads/:id/:action', ({ params }) => { calls.push(`${params.id}/${params.action}`); return HttpResponse.json(job()); }),
    );
    const user = userEvent.setup();
    renderPage(<DownloadsPage />);
    expect(await screen.findByText('Job Video')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: /progress for job video/i })).toHaveAttribute('aria-valuenow', '42');
    expect(await screen.findByRole('tab', { name: /queued 2/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Pause Job Video' }));
    await waitFor(() => expect(calls).toContain('1/pause'));

    await user.click(screen.getByRole('tab', { name: /failed/i }));
    expect(await screen.findByText(/network down/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry Broken' }));
    await waitFor(() => expect(calls).toContain('7/retry'));
  });

  it('extracts the video ID from a URL before enqueueing', async () => {
    let body: unknown;
    server.use(
      http.get('/api/downloads/counts', () => HttpResponse.json(counts)),
      http.get('/api/downloads', () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 })),
      http.post('/api/downloads', async ({ request }) => { body = await request.json(); return HttpResponse.json(job()); }),
    );
    const user = userEvent.setup();
    renderPage(<DownloadsPage />);
    await user.type(screen.getByLabelText('Video ID or URL'), 'https://youtu.be/dQw4w9WgXcQ?si=x');
    await user.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(body).toEqual({ video_id: 'dQw4w9WgXcQ', max_height: null }));
  });
});
