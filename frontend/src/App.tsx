import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { MeProvider, useSession } from './auth';
import { Layout } from './components/Layout';
import { AckDialog } from './components/AckDialog';
import { Spinner } from './components/ui';
import { LoginPage } from './pages/Login';

const DashboardPage = lazy(() => import('./pages/Dashboard'));
const SubscriptionsPage = lazy(() => import('./pages/Subscriptions'));
const LikesPage = lazy(() => import('./pages/Likes'));
const DownloadsPage = lazy(() => import('./pages/Downloads'));
const LibraryPage = lazy(() => import('./pages/Library'));
const SettingsPage = lazy(() => import('./pages/Settings'));

export function App() {
  const session = useSession();
  if (session.isLoading) return <Spinner />;
  const me = session.data ?? null;
  return (
    <Routes>
      <Route path="/login" element={me ? <Navigate to="/" replace /> : <LoginPage />} />
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
