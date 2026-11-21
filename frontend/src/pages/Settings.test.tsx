import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { renderPage } from '../test/utils';
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
