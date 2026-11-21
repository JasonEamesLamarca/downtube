import { http, HttpResponse } from 'msw';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { setupStatus } from '../test/fixtures';
import { App } from '../App';

function renderApp(path = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

describe('setup gate and page', () => {
  it('redirects every route to /setup without calling /auth/me', async () => {
    let meCalled = false;
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json(setupStatus())),
      http.get('/api/auth/me', () => { meCalled = true; return HttpResponse.json({}, { status: 401 }); }),
    );
    renderApp('/subscriptions');
    expect(await screen.findByRole('heading', { name: 'Set up DownTube' })).toBeInTheDocument();
    expect(meCalled).toBe(false);
  });

  it('redirects /setup to /login when setup is done', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json(setupStatus({ needs_setup: false }))),
      http.get('/api/auth/me', () => HttpResponse.json({}, { status: 401 })),
    );
    renderApp('/setup');
    expect(await screen.findByRole('link', { name: 'Sign in with Google' })).toBeInTheDocument();
  });

  it('submits and lands on /login with success banner', async () => {
    let body: unknown = null;
    let done = false;
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json(setupStatus({ needs_setup: !done }))),
      http.get('/api/auth/me', () => HttpResponse.json({}, { status: 401 })),
      http.post('/api/setup', async ({ request }) => { body = await request.json(); done = true; return HttpResponse.json(setupStatus({ needs_setup: false })); }),
    );
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText('Setup code'), 'abc123');
    await user.type(screen.getByLabelText('Client ID'), 'cid');
    await user.type(screen.getByLabelText('Client secret'), 'sec');
    await user.type(screen.getByLabelText('Allowed accounts'), 'A@B.com, a@b.com, c@d.org,');
    await user.click(screen.getByRole('button', { name: 'Complete setup' }));
    expect(await screen.findByText(/Setup complete/)).toBeInTheDocument();
    expect(body).toEqual({ setup_code: 'abc123', google_client_id: 'cid', google_client_secret: 'sec', allowed_emails: ['a@b.com', 'c@d.org'], admin_emails: ['a@b.com'] });
  });

  it('shows invalid setup code error on the code field', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json(setupStatus())),
      http.post('/api/setup', () => HttpResponse.json({ error: { code: 'invalid_setup_code', message: 'bad' } }, { status: 403 })),
    );
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText('Setup code'), 'wrong1');
    await user.type(screen.getByLabelText('Allowed accounts'), 'a@b.com,');
    await user.click(screen.getByRole('button', { name: 'Complete setup' }));
    await waitFor(() => expect(screen.getByText(/Invalid setup code/)).toBeInTheDocument());
  });

  it('hides client inputs when google is env managed', async () => {
    server.use(http.get('/api/setup/status', () => HttpResponse.json(setupStatus({ google_env_managed: true }))));
    renderApp('/');
    expect(await screen.findByText('Configured by the server environment')).toBeInTheDocument();
    expect(screen.queryByLabelText('Client ID')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Client secret')).not.toBeInTheDocument();
  });
});
