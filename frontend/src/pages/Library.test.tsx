import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { MediaFileOut } from '../api/types';
import { server } from '../test/server';
import { ME, renderPage } from '../test/utils';
import LibraryPage from './Library';

const item = (over: Partial<MediaFileOut> = {}): MediaFileOut => ({
  id: 1, video_id: 'dQw4w9WgXcQ', title: 'Liked Video', channel_id: 'UC1', channel_title: 'Chan', thumbnail_url: null,
  url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', rel_path: 'Chan/x.mp4', container: 'mp4', width: 1920, height: 1080,
  fps: 30, vcodec: 'avc1', acodec: 'mp4a', duration_s: 60, size_bytes: 1000, is_hdr: false, source_format_id: '137+140',
  max_height_requested: 2160, created_at: '2026-01-01T00:00:00Z', published_at: '2025-06-15T12:00:00Z', sources: ['like'], ...over,
});

const channels = http.get('/api/library/channels', () => HttpResponse.json([
  { channel_id: 'UCa', title: 'Alpha Channel', count: 3 },
  { channel_id: 'UCz', title: 'Zulu Channel', count: 1 },
]));
const library = (items: MediaFileOut[]) => () => HttpResponse.json({ items, total: items.length, page: 1, page_size: 24 });

describe('LibraryPage delete', () => {
  it('deletes a liked video and removes the like when permitted', async () => {
    const bodies: unknown[] = [];
    server.use(
      channels, http.get('/api/library', library([item()])),
      http.post('/api/library/dQw4w9WgXcQ/delete', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ video_id: 'dQw4w9WgXcQ', deleted_files: ['Chan/x.mp4', 'Chan/x.nfo'], like_removed: true, excluded: true, warnings: [] });
      }),
    );
    const user = userEvent.setup();
    renderPage(<LibraryPage />, { ...ME, can_manage_youtube: true });
    await user.click(await screen.findByRole('button', { name: 'Delete Liked Video' }));
    const box = screen.getByRole('checkbox', { name: /remove the like/i });
    expect(box).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Delete video' }));
    await waitFor(() => expect(bodies).toEqual([{ remove_like: true }]));
    expect(await screen.findByRole('status')).toHaveTextContent('Deleted 2 files and removed the like on YouTube');
  });

  it('keeps the like and offers the permission when it is missing', async () => {
    const bodies: unknown[] = [];
    server.use(
      channels, http.get('/api/library', library([item()])),
      http.post('/api/library/dQw4w9WgXcQ/delete', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ video_id: 'dQw4w9WgXcQ', deleted_files: ['Chan/x.mp4'], like_removed: false, excluded: true, warnings: ['the video is still liked on YouTube'] });
      }),
    );
    const user = userEvent.setup();
    renderPage(<LibraryPage />);
    await user.click(await screen.findByRole('button', { name: 'Delete Liked Video' }));
    expect(screen.getByRole('checkbox', { name: /remove the like/i })).toBeDisabled();
    expect(screen.getByRole('link', { name: /grant permission/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Delete video' }));
    await waitFor(() => expect(bodies).toEqual([{ remove_like: false }]));
  });

  it('explains that channel videos will not be downloaded again', async () => {
    server.use(channels, http.get('/api/library', library([item({ title: 'Channel Video', sources: ['subscription'] })])));
    const user = userEvent.setup();
    renderPage(<LibraryPage />);
    await user.click(await screen.findByRole('button', { name: 'Delete Channel Video' }));
    const dialog = screen.getByRole('dialog', { name: /delete from library/i });
    expect(dialog).toHaveTextContent('even though its channel is archived');
    expect(screen.queryByRole('checkbox', { name: /remove the like/i })).not.toBeInTheDocument();
  });

  it('lists every library channel in the filter and filters by it', async () => {
    const seen: (string | null)[] = [];
    server.use(channels, http.get('/api/library', ({ request }) => {
      seen.push(new URL(request.url).searchParams.get('channel_id'));
      return HttpResponse.json({ items: [item()], total: 1, page: 1, page_size: 24 });
    }));
    const user = userEvent.setup();
    renderPage(<LibraryPage />);
    const select = await screen.findByLabelText('Channel');
    await screen.findByRole('option', { name: 'Zulu Channel (1)' });
    expect([...(select as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(['All channels', 'Alpha Channel (3)', 'Zulu Channel (1)']);
    await user.selectOptions(select, 'UCz');
    await waitFor(() => expect(seen).toContain('UCz'));
  });

  it('labels vertical videos by their short side and keeps the title in list view', async () => {
    server.use(channels, http.get('/api/library', library([item({ title: 'Vertical Short', width: 1080, height: 1920, vcodec: 'vp9' })])));
    const user = userEvent.setup();
    renderPage(<LibraryPage />);
    expect(await screen.findByText(/^1080p · vp9/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'List' }));
    const title = screen.getByText('Vertical Short');
    expect(title.closest('div')?.className).toContain('flex-1');
    expect(screen.getByRole('button', { name: 'Details for Vertical Short' }).className).toContain('shrink-0');
  });
});

describe('LibraryPage release dates', () => {
  it('sorts by release date by default and can switch to date downloaded', async () => {
    const queries: string[] = [];
    server.use(channels, http.get('/api/library', ({ request }) => {
      const u = new URL(request.url);
      queries.push(`${u.searchParams.get('sort')}:${u.searchParams.get('order')}`);
      return library([item()])();
    }));
    const user = userEvent.setup();
    renderPage(<LibraryPage />);
    expect(await screen.findByText(/Released/)).toHaveTextContent(new Date('2025-06-15T12:00:00Z').toLocaleDateString());
    expect(queries[0]).toBe('published:desc');
    await user.selectOptions(screen.getByLabelText('Sort by'), 'downloaded');
    await waitFor(() => expect(queries.at(-1)).toBe('downloaded:desc'));
  });
});
