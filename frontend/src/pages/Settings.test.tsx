import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { ME, renderPage } from '../test/utils';
import { settings } from '../test/fixtures';
import SettingsPage, { diffSettings } from './Settings';

describe('diffSettings', () => {
  it('only includes changed fields and applies wire conventions', () => {
    expect(diffSettings(settings, { ...settings, bandwidth_limit_kbps: 500 })).toEqual({ bandwidth_limit_kbps: 500 });
    expect(diffSettings({ ...settings, bandwidth_limit_kbps: 500 }, settings)).toEqual({ bandwidth_limit_kbps: 0 });
    expect(diffSettings({ ...settings, download_window: '01:00-02:00' }, { ...settings, download_window: null })).toEqual({ download_window: '' });
    expect(diffSettings(settings, { ...settings, archive_root: '/other' })).toEqual({});
  });
});

describe('SettingsPage', () => {
  it('saves only changed fields', async () => {
    let body: unknown = null;
    server.use(
      http.get('/api/settings', () => HttpResponse.json(settings)),
      http.get('/api/storage', () => HttpResponse.json({ root_path: '/archive', host_path_hint: '/volume1/video', total_bytes: 100, free_bytes: 50, archive_bytes: 10, min_free_bytes: 1, below_threshold: false, writable: true })),
      http.put('/api/settings', async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...settings, max_concurrent_downloads: 4 }); }),
    );
    const user = userEvent.setup();
    renderPage(<SettingsPage />);
    const save = await screen.findByRole('button', { name: 'Save changes' });
    expect(save).toBeDisabled();
    const input = screen.getByLabelText('Concurrent downloads');
    await user.clear(input);
    await user.type(input, '4');
    await user.click(save);
    await waitFor(() => expect(body).toEqual({ max_concurrent_downloads: 4 }));
    expect(await screen.findByText('Settings saved.')).toBeInTheDocument();
  });

  it('shows 422 field errors', async () => {
    server.use(
      http.get('/api/settings', () => HttpResponse.json(settings)),
      http.get('/api/storage', () => HttpResponse.json({ root_path: '/archive', host_path_hint: null, total_bytes: 100, free_bytes: 50, archive_bytes: 10, min_free_bytes: 1, below_threshold: false, writable: true })),
      http.put('/api/settings', () => HttpResponse.json({ error: { code: 'validation_error', message: 'bad', details: [{ loc: ['body', 'min_free_gb'], msg: 'must be >= 1' }] } }, { status: 422 })),
    );
    const user = userEvent.setup();
    renderPage(<SettingsPage />);
    const input = await screen.findByLabelText(/Keep at least this much free space/);
    await user.clear(input);
    await user.type(input, '0');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText('must be >= 1')).toBeInTheDocument();
  });
});

describe('Google section', () => {
  const g = { google_client_id: 'cid', google_client_secret_set: true, allowed_emails: ['a@b.c'], admin_emails: ['a@b.c'], google_env_managed: false, emails_env_managed: false, redirect_uri: 'http://x/cb' };
  const base = () => server.use(
    http.get('/api/settings', () => HttpResponse.json(settings)),
    http.get('/api/storage', () => HttpResponse.json({ root_path: '/archive', host_path_hint: null, total_bytes: 100, free_bytes: 50, archive_bytes: 10, min_free_bytes: 1, below_threshold: false, writable: true })),
  );

  it('saves only changed fields', async () => {
    base();
    let body: unknown = null;
    server.use(
      http.get('/api/admin/google', () => HttpResponse.json(g)),
      http.put('/api/admin/google', async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...g, allowed_emails: ['a@b.c', 'n@e.w'] }); }),
    );
    const user = userEvent.setup();
    renderPage(<SettingsPage />);
    await user.type(await screen.findByLabelText('Allowed Google accounts'), 'N@E.W,');
    await user.click(screen.getByRole('button', { name: 'Save Google settings' }));
    await waitFor(() => expect(body).toEqual({ allowed_emails: ['a@b.c', 'n@e.w'] }));
  });

  it('shows cannot_remove_self message', async () => {
    base();
    server.use(
      http.get('/api/admin/google', () => HttpResponse.json(g)),
      http.put('/api/admin/google', () => HttpResponse.json({ error: { code: 'cannot_remove_self', message: 'x' } }, { status: 409 })),
    );
    const user = userEvent.setup();
    renderPage(<SettingsPage />);
    await screen.findByLabelText('Allowed Google accounts');
    await user.click(screen.getByRole('button', { name: 'Remove a@b.c' }));
    await user.click(screen.getByRole('button', { name: 'Save Google settings' }));
    expect(await screen.findByText("You can't remove your own account")).toBeInTheDocument();
  });

  it('is hidden for non-admins', async () => {
    base();
    renderPage(<SettingsPage />, { ...ME, is_admin: false });
    await screen.findByRole('button', { name: 'Save changes' });
    expect(screen.queryByText(/Google sign-in & access/)).not.toBeInTheDocument();
  });
});
