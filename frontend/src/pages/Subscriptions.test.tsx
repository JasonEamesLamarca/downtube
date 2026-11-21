import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { server } from '../test/server';
import { renderPage } from '../test/utils';
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
});
