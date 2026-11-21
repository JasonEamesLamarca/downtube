import { createContext, useContext, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, setApiHooks, setCsrfToken } from './api/client';
import { endpoints } from './api/endpoints';
import type { Me } from './api/types';

const MeContext = createContext<Me | null>(null);
export const useMe = (): Me => {
  const m = useContext(MeContext);
  if (!m) throw new Error('useMe outside authenticated tree');
  return m;
};
export const MeProvider = MeContext.Provider;

export const ME_KEY = ['me'];

/** Loads the session; resolves to null when unauthenticated. */
export function useSession() {
  const qc = useQueryClient();
  const q = useQuery<Me | null>({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await endpoints.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });
  useEffect(() => { setCsrfToken(q.data?.csrf_token ?? null); }, [q.data]);
  useEffect(() => {
    setApiHooks({
      onUnauthorized: () => qc.setQueryData(ME_KEY, null),
      onAcknowledgementRequired: () =>
        qc.setQueryData<Me | null>(ME_KEY, (m) => (m ? { ...m, acknowledged_terms: false } : m)),
    });
  }, [qc]);
  return q;
}
