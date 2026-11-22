import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { renderPage } from '../test/utils';
import { job } from '../test/fixtures';
import DownloadsPage from './Downloads';
import { setCsrfToken } from '../api/client';

const folder = { path: '/volume1/video/DownTube/.import', files: [] };
const counts = { queued: 2, running: 1, paused: 0, completed: 9, failed: 1, cancelled: 0, unsupported: 0, skipped: 0 };

describe('DownloadsPage', () => {
  it('shows counts, progress and posts job actions', async () => {
    const calls: string[] = [];
    server.use(
      http.get('/api/downloads/counts', () => HttpResponse.json(counts)),
      http.get('/api/imports', () => HttpResponse.json(folder)),
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
      http.get('/api/imports', () => HttpResponse.json(folder)),
      http.get('/api/downloads', () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 })),
      http.post('/api/downloads', async ({ request }) => { body = await request.json(); return HttpResponse.json(job()); }),
    );
    const user = userEvent.setup();
    renderPage(<DownloadsPage />);
    await user.type(screen.getByLabelText('Video ID or URL'), 'https://youtu.be/dQw4w9WgXcQ?si=x');
    await user.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(body).toEqual({ video_id: 'dQw4w9WgXcQ', max_height: null }));
  });

  it('links to YouTube and imports an age-restricted video the user downloaded', async () => {
    let uploaded: { filename: string | null; size: number; csrf: string | null } | null = null;
    const failed = job({ id: 9, video_id: 'dQw4w9WgXcQ', status: 'failed', title: 'Grown-up', error_code: 'age_restricted', error_message: 'Sign in to confirm your age', max_height: 1080 });
    server.use(
      http.get('/api/downloads/counts', () => HttpResponse.json(counts)),
      http.get('/api/imports', () => HttpResponse.json({ ...folder, files: [{ name: 'x.mp4', size_bytes: 10, video_id: null, status: 'failed', message: 'the file name must contain the video ID in brackets' }] })),
      http.get('/api/downloads', ({ request }) => {
        const items = new URL(request.url).searchParams.get('status') === 'failed' ? [failed] : [];
        return HttpResponse.json({ items, total: items.length, page: 1, page_size: 25 });
      }),
      http.put('/api/downloads/:id/import', async ({ request }) => {
        const url = new URL(request.url);
        uploaded = { filename: url.searchParams.get('filename'), size: (await request.arrayBuffer()).byteLength, csrf: request.headers.get('x-csrf-token') };
        return HttpResponse.json({ ...failed, status: 'completed', error_code: null, error_message: null, selection: { imported: true } });
      }),
    );
    localStorage.clear();
    setCsrfToken('tok-123');
    const user = userEvent.setup();
    renderPage(<DownloadsPage />);
    expect(await screen.findByText(/Not imported: the file name must contain/)).toBeInTheDocument();
    expect(screen.getByText('/volume1/video/DownTube/.import')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /failed/i }));
    expect(await screen.findByText(/needs a signed-in account/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Grown-up on YouTube' })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ');

    await user.click(screen.getByRole('button', { name: 'Download Grown-up myself' }));
    const dialog = await screen.findByRole('dialog', { name: /download it with your account/i });
    const cmd = screen.getByLabelText('yt-dlp command');
    expect(cmd.textContent).toContain('--cookies-from-browser chrome');
    expect(cmd.textContent).toContain('-S "res:1080"');
    expect(cmd.textContent).toContain('"https://www.youtube.com/watch?v=dQw4w9WgXcQ"');
    await user.selectOptions(screen.getByLabelText('Browser'), 'firefox');
    expect(screen.getByLabelText('yt-dlp command').textContent).toContain('--cookies-from-browser firefox');
    expect(screen.getByLabelText('Keyring (Linux)')).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Browser'), 'chromium');
    await user.selectOptions(screen.getByLabelText('Keyring (Linux)'), 'gnomekeyring');
    await user.selectOptions(screen.getByLabelText('JavaScript runtime'), 'node');
    expect(screen.getByLabelText('yt-dlp command').textContent).toContain('--cookies-from-browser chromium+gnomekeyring --js-runtimes node -S');
    expect(JSON.parse(localStorage.getItem('downtube.ytdlp-options') ?? '{}')).toEqual({ browser: 'chromium', keyring: 'gnomekeyring', js: 'node' });
    expect(dialog.textContent).toContain('/volume1/video/DownTube/.import');

    const file = new File([new Uint8Array(2048)], 'Grown-up [dQw4w9WgXcQ].mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('Video file'), file);
    await user.click(screen.getByRole('button', { name: /^Upload/ }));
    expect(await screen.findByText('Imported into the archive.')).toBeInTheDocument();
    expect(uploaded).toEqual({ filename: 'Grown-up [dQw4w9WgXcQ].mp4', size: 2048, csrf: 'tok-123' });
    setCsrfToken(null);
  });

  it('scans the import folder on request', async () => {
    let scanned = false;
    server.use(
      http.get('/api/downloads/counts', () => HttpResponse.json(counts)),
      http.get('/api/downloads', () => HttpResponse.json({ items: [], total: 0, page: 1, page_size: 25 })),
      http.get('/api/imports', () => HttpResponse.json(folder)),
      http.post('/api/imports/scan', () => { scanned = true; return HttpResponse.json({ ...folder, files: [{ name: 'a [dQw4w9WgXcQ].mp4', size_bytes: 1, video_id: 'dQw4w9WgXcQ', status: 'imported', message: null }] }); }),
    );
    const user = userEvent.setup();
    renderPage(<DownloadsPage />);
    await user.click(await screen.findByRole('button', { name: 'Scan now' }));
    expect(await screen.findByText('Imported 1 file.')).toBeInTheDocument();
    expect(scanned).toBe(true);
  });
});
