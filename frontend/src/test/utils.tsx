import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MeProvider } from '../auth';
import type { Me } from '../api/types';

export const ME: Me = {
  id: 1, email: 'a@b.c', display_name: 'A', picture_url: null, is_admin: true,
  acknowledged_terms: true, account_status: 'active', youtube_channel_id: null, csrf_token: 'tok',
};

export function renderPage(ui: ReactElement, me: Me = ME) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><MeProvider value={me}>{ui}</MeProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}
