import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { ME, renderPage } from '../test/utils';
import { sub } from '../test/fixtures';
import SubscriptionsPage from './Subscriptions';

const page = (items = [sub(), sub({ channel_id: 'UC2', title: 'Channel Two' })]) =>
  HttpResponse.json({ items, total: items.length, page: 1, page_size: 25 });

describe('SubscriptionsPage', () => {
  it('renders rows from the API', async () => {
    server.use(http.get('/api/subscriptions', () => page()));
    renderPage(<SubscriptionsPage />);
    expect(await screen.findByText('Channel One')).toBeInTheDocument();
    expect(screen.getByText('Channel Two')).toBeInTheDocument();
    expect(screen.getByLabelText('Mode for Channel One')).toHaveValue('off');
  });

  it('asks for confirmation on large channels and resends with confirm_large', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.get('/api/subscriptions', () => page([sub()])),
      http.patch('/api/subscriptions/UC1/rule', async ({ request }) => {
        const body = (await request.json()) as { confirm_large?: boolean };
        bodies.push(body);
        if (!body.confirm_large) {
          return HttpResponse.json({ error: { code: 'confirmation_required', message: 'large', details: {
            channel_id: 'UC1', video_count: 4321, estimated_bytes: 5 * 1024 ** 3, estimate_basis: 'avg 1.2 GB per 1080p hour', is_large: true, requires_confirmation: true } } }, { status: 409 });
        }
        return HttpResponse.json(sub({ rule: { ...sub().rule, mode: 'backfill_once' } }));
      }),
    );
    const user = userEvent.setup();
    renderPage(<SubscriptionsPage />);
    await user.selectOptions(await screen.findByLabelText('Mode for Channel One'), 'backfill_once');
    const dialog = await screen.findByRole('dialog', { name: /large channel/i });
    expect(dialog).toHaveTextContent('4,321');
    expect(dialog).toHaveTextContent('5.0 GB');
    expect(dialog).toHaveTextContent('avg 1.2 GB per 1080p hour');
    await user.click(screen.getByRole('button', { name: /confirm and start/i }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0]).toEqual({ mode: 'backfill_once' });
    expect(bodies[1]).toEqual({ mode: 'backfill_once', confirm_large: true });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('unsubscribes after confirmation when the manage permission is granted', async () => {
    let called = 0;
    server.use(
      http.get('/api/subscriptions', () => page([sub()])),
      http.post('/api/subscriptions/UC1/unsubscribe', () => { called += 1; return HttpResponse.json(sub({ subscribed: false })); }),
    );
    const user = userEvent.setup();
    renderPage(<SubscriptionsPage />, { ...ME, can_manage_youtube: true });
    await user.click(await screen.findByRole('button', { name: 'Unsubscribe from Channel One' }));
    const dialog = await screen.findByRole('dialog', { name: /unsubscribe from channel one/i });
    expect(dialog).toHaveTextContent('stay in your library');
    await user.click(screen.getByRole('button', { name: 'Unsubscribe' }));
    await waitFor(() => expect(called).toBe(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('asks for the manage permission before unsubscribing', async () => {
    server.use(http.get('/api/subscriptions', () => page([sub()])));
    const user = userEvent.setup();
    renderPage(<SubscriptionsPage />);
    await user.click(await screen.findByRole('button', { name: 'Unsubscribe from Channel One' }));
    expect(screen.getByRole('button', { name: 'Unsubscribe' })).toBeDisabled();
    expect(screen.getByRole('link', { name: /grant permission/i })).toHaveAttribute('href', '/api/auth/login?manage=true');
  });

  it('marks channels you are no longer subscribed to', async () => {
    server.use(http.get('/api/subscriptions', () => page([sub({ subscribed: false })])));
    renderPage(<SubscriptionsPage />);
    expect(await screen.findByText('Unsubscribed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unsubscribe from Channel One' })).not.toBeInTheDocument();
  });

  it('deletes an unsubscribed channel and keeps its archive', async () => {
    let called = 0;
    server.use(
      http.get('/api/subscriptions', () => page([sub({ subscribed: false, archived_count: 7 })])),
      http.post('/api/subscriptions/UC1/delete', () => {
        called += 1;
        return HttpResponse.json({ channel_id: 'UC1', title: 'Channel One', archived_kept: 7, jobs_cancelled: 0 });
      }),
    );
    const user = userEvent.setup();
    renderPage(<SubscriptionsPage />);
    await user.click(await screen.findByRole('button', { name: 'Delete Channel One' }));
    const dialog = await screen.findByRole('dialog', { name: /delete channel one/i });
    expect(dialog).toHaveTextContent('Videos already archived (7) stay in your library');
    expect(dialog).toHaveTextContent('subscribe to the channel again');
    await user.click(screen.getByRole('button', { name: 'Delete subscription' }));
    await waitFor(() => expect(called).toBe(1));
    expect(await screen.findByRole('status')).toHaveTextContent('Deleted Channel One. 7 archived videos kept in your library.');
  });

  it('has no delete button while still subscribed and filters by subscription state', async () => {
    const seen: (string | null)[] = [];
    server.use(http.get('/api/subscriptions', ({ request }) => {
      seen.push(new URL(request.url).searchParams.get('subscribed'));
      return page([sub()]);
    }));
    const user = userEvent.setup();
    renderPage(<SubscriptionsPage />);
    await screen.findByText('Channel One');
    expect(screen.queryByRole('button', { name: 'Delete Channel One' })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Filter by subscription'), 'false');
    await waitFor(() => expect(seen.at(-1)).toBe('false'));
  });
});
