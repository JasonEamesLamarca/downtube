import { useSearchParams } from 'react-router-dom';

const MESSAGES: Record<string, string> = {
  access_denied: 'Google sign-in was cancelled or denied.',
  not_allowed: 'This Google account is not on the allowed list for this DownTube instance.',
  invalid_state: 'The sign-in attempt expired or was invalid. Please try again.',
};

export function LoginPage() {
  const [params] = useSearchParams();
  const error = params.get('error');
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="card w-full max-w-sm space-y-4 text-center">
        <h1 className="text-3xl font-bold text-brand-600">DownTube</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">Self-hosted archive for your liked videos and subscriptions.</p>
        {error && (
          <p role="alert" className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            {MESSAGES[error] ?? `Sign-in failed: ${error}`}
          </p>
        )}
        <a href="/api/auth/login" className="btn btn-primary w-full py-2">Sign in with Google</a>
        <p className="text-xs text-slate-500">DownTube is an independent self-hosted tool and is not affiliated with, endorsed by, or sponsored by Google or YouTube.</p>
      </div>
    </main>
  );
}
