import { lazy, Suspense, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { MeProvider, useSession } from './auth';
import { Layout } from './components/Layout';
import { AckDialog } from './components/AckDialog';
import { Spinner } from './components/ui';
import { LoginPage } from './pages/Login';
import { SetupPage } from './pages/Setup';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from './api/endpoints';

const DashboardPage = lazy(() => import('./pages/Dashboard'));
const SubscriptionsPage = lazy(() => import('./pages/Subscriptions'));
const LikesPage = lazy(() => import('./pages/Likes'));
const DownloadsPage = lazy(() => import('./pages/Downloads'));
const LibraryPage = lazy(() => import('./pages/Library'));
const SettingsPage = lazy(() => import('./pages/Settings'));

export function App() {
  const [justSetUp, setJustSetUp] = useState(false);
  const setup = useQuery({ queryKey: ['setup-status'], queryFn: endpoints.setupStatus, staleTime: 0 });
  if (setup.isLoading) return <Spinner />;
  if (setup.data?.needs_setup) {
    return (
      <Routes>
        <Route path="/setup" element={<SetupPage status={setup.data} onComplete={() => setJustSetUp(true)} />} />
        <Route path="*" element={<Navigate to="/setup" replace />} />
      </Routes>
    );
  }
  return <ConfiguredApp setupComplete={justSetUp} />;
}

function ConfiguredApp({ setupComplete }: { setupComplete: boolean }) {
  const session = useSession();
  if (session.isLoading) return <Spinner />;
  const me = session.data ?? null;
  return (
    <Routes>
      <Route path="/setup" element={<Navigate to={me ? '/' : '/login'} replace />} />
      <Route path="/login" element={me ? <Navigate to="/" replace /> : <LoginPage setupComplete={setupComplete} />} />
      <Route
        element={me ? (
          <MeProvider value={me}>
            <Layout />
            {!me.acknowledged_terms && <AckDialog />}
          </MeProvider>
        ) : <Navigate to="/login" replace />}
      >
        <Route index element={<Suspense fallback={<Spinner />}><DashboardPage /></Suspense>} />
        <Route path="subscriptions" element={<Suspense fallback={<Spinner />}><SubscriptionsPage /></Suspense>} />
        <Route path="likes" element={<Suspense fallback={<Spinner />}><LikesPage /></Suspense>} />
        <Route path="downloads" element={<Suspense fallback={<Spinner />}><DownloadsPage /></Suspense>} />
        <Route path="library" element={<Suspense fallback={<Spinner />}><LibraryPage /></Suspense>} />
        <Route path="settings" element={<Suspense fallback={<Spinner />}><SettingsPage /></Suspense>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
