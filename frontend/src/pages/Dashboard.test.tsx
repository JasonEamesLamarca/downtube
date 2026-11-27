import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { renderPage } from '../test/utils';
import type { Dashboard } from '../api/types';
import DashboardPage from './Dashboard';
import { setCsrfToken } from '../api/client';

function dashboard(over: Partial<Dashboard> = {}): Dashboard {
  return {
    account_email: 'a@b.c', account_status: 'active', archived_videos: 3, archive_bytes: 1000, monitored_channels: 1,
    liked_videos_discovered: 2, downloads_active: 0, downloads_pending: 5, downloads_failed: 0, current_speed_bps: 0,
    storage: { root_path: '/archive', host_path_hint: null, total_bytes: 100, free_bytes: 50, archive_bytes: 10, min_free_bytes: 1, below_threshold: false, writable: true },
    quota: { day: '2026-11-27', used: 10, budget: 9000, exhausted: false, resets_at: '2026-11-28T08:00:00Z' },
    likes_mode: 'future_only', last_likes_sync: null, recent_syncs: [],
    ...over,
  };
}

describe('DashboardPage bot-check banner', () => {
  it('shows the pause and ends it with "Try again now"', async () => {
    setCsrfToken('tok');
    let paused = true;
    let posts = 0;
    server.use(
      http.get('/api/dashboard', () => HttpResponse.json(dashboard(paused
        ? { downloads_paused_until: new Date(Date.now() + 3600_000).toISOString(), bot_checks_in_a_row: 2 }
        : {}))),
      http.post('/api/downloads/bot-check/retry-now', () => { posts += 1; paused = false; return HttpResponse.json({ resumed: true }); }),
    );
    const user = userEvent.setup();
    renderPage(<DashboardPage />);
    expect(await screen.findByText(/confirm it is not a bot/i)).toBeInTheDocument();
    expect(screen.getByText(/2 times in a row/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again now' }));
    await waitFor(() => expect(posts).toBe(1));
    expect(await screen.findByText(/Downloads resumed/)).toBeInTheDocument();
    expect(screen.queryByText(/confirm it is not a bot/i)).not.toBeInTheDocument();
  });

  it('shows no banner when downloads are not paused', async () => {
    server.use(http.get('/api/dashboard', () => HttpResponse.json(dashboard())));
    renderPage(<DashboardPage />);
    expect(await screen.findByText('Archived videos')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again now' })).not.toBeInTheDocument();
  });
});
