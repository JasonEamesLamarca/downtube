import { useSearchParams } from 'react-router-dom';
import { LogoMark } from '../components/ui';

const MESSAGES: Record<string, string> = {
  access_denied: 'Google sign-in was cancelled or denied.',
  not_allowed: 'This Google account is not on the allowed list for this DownTube instance.',
  email_not_allowed: 'This Google account is not on the allowed list for this DownTube instance.',
  email_not_verified: 'Your Google account email address is not verified.',
  missing_scope: 'DownTube needs read-only access to your YouTube account. Please sign in again and allow it.',
  state_expired: 'The sign-in attempt expired. Please try again.',
  nonce_mismatch: 'The sign-in response could not be verified. Please try again.',
  invalid_id_token: 'The sign-in response could not be verified. Please try again.',
  token_exchange_failed: 'Google sign-in failed. Check the OAuth client ID, secret and redirect URI.',
  oauth_not_configured: 'Google sign-in is not configured on this server (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).',
  oauth_error: 'Google returned an error during sign-in.',
  invalid_request: 'The sign-in response was incomplete. Please try again.',
  invalid_state: 'The sign-in attempt expired or was invalid. Please try again.',
};

export function LoginPage({ setupComplete = false }: { setupComplete?: boolean }) {
  const [params] = useSearchParams();
  const error = params.get('error');
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="card w-full max-w-sm space-y-4 text-center">
        <h1 className="flex items-center justify-center gap-3 text-3xl font-bold text-brand-600"><LogoMark size={44} />DownTube</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">Self-hosted archive for your liked videos and subscriptions.</p>
        {setupComplete && (
          <p role="status" className="rounded-sm border border-green-300 bg-green-50 p-2 text-sm text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200">
            Setup complete — sign in with Google
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-sm border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {MESSAGES[error] ?? `Sign-in failed: ${error}`}
          </p>
        )}
        <a href="/api/auth/login" className="btn btn-primary w-full py-2">Sign in with Google</a>
        <p className="text-xs text-slate-500">DownTube is an independent self-hosted tool and is not affiliated with, endorsed by, or sponsored by Google or YouTube.</p>
      </div>
    </main>
  );
}
