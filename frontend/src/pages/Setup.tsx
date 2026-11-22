import { useId, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { endpoints } from '../api/endpoints';
import type { SetupStatus } from '../api/types';
import { AdminPicker, CopyField, EmailChips, GoogleInstructions, SecretInput } from '../components/GoogleConfig';
import { fieldErrors } from '../lib/errors';

export function SetupPage({ status, onComplete }: { status: SetupStatus; onComplete?: () => void }) {
  const uid = useId();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [code, setCode] = useState('');
  const [clientId, setClientId] = useState('');
  const [secret, setSecret] = useState('');
  const [allowed, setAllowed] = useState<string[]>([]);
  const [adminSel, setAdminSel] = useState<string[] | null>(null);
  const admins = (adminSel ?? allowed.slice(0, 1)).filter((a) => allowed.includes(a));
  const [formError, setFormError] = useState('');
  const [codeError, setCodeError] = useState('');
  const [errs, setErrs] = useState<Record<string, string>>({});

  const m = useMutation({
    mutationFn: () => endpoints.submitSetup({
      setup_code: code.trim(),
      google_client_id: status.google_env_managed ? null : clientId.trim() || null,
      google_client_secret: status.google_env_managed ? null : secret || null,
      allowed_emails: status.emails_env_managed ? [] : allowed,
      admin_emails: status.emails_env_managed ? [] : admins,
    }),
    onSuccess: (r) => {
      onComplete?.();
      nav('/login', { replace: true });
      qc.setQueryData(['setup-status'], { ...r, needs_setup: false });
    },
    onError: (e) => {
      setFormError(''); setCodeError(''); setErrs({});
      if (e instanceof ApiError) {
        if (e.status === 403 && e.code === 'invalid_setup_code') return setCodeError('Invalid setup code. Check the container log for the latest one.');
        if (e.status === 429) return setFormError('Too many attempts, wait a minute and try again.');
        if (e.status === 409) { void qc.invalidateQueries({ queryKey: ['setup-status'] }); return nav('/login', { replace: true }); }
        if (e.status === 422) {
          const f = fieldErrors(e);
          setErrs(f);
          if (Object.keys(f).length === 0) setFormError(e.message);
          return;
        }
        return setFormError(e.message);
      }
      setFormError('Could not reach the server.');
    },
  });

  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    setFormError(''); setCodeError(''); setErrs({});
    if (!status.emails_env_managed && allowed.length === 0) { setFormError('Add at least one allowed Google account.'); return; }
    m.mutate();
  };
  const err = (k: string) => errs[k] && <p role="alert" className="text-xs text-red-700 dark:text-red-400">{errs[k]}</p>;

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-xl space-y-5" noValidate>
        <h1 className="text-3xl font-bold text-brand-600">Set up DownTube</h1>

        <section className="space-y-2" aria-label="Setup code step">
          <label htmlFor={`${uid}-code`} className="block text-sm font-medium">Setup code</label>
          <input id={`${uid}-code`} className="input w-full font-mono" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} />
          <p className="text-xs text-slate-500">
            Find it in the container log: <code>docker logs downtube | grep 'setup code'</code> — a new code is printed every time the container starts until setup is complete.
          </p>
          {codeError && <p role="alert" className="text-xs text-red-700 dark:text-red-400">{codeError}</p>}
          {err('setup_code')}
        </section>

        <section className="space-y-2" aria-label="Google sign-in client">
          <h2 className="text-lg font-semibold">Google sign-in client</h2>
          {status.google_env_managed ? (
            <p role="note" className="text-sm text-slate-600 dark:text-slate-400">Configured by the server environment</p>
          ) : (
            <>
              <GoogleInstructions redirectUri={status.redirect_uri} />
              <CopyField label="Authorized redirect URI" value={status.redirect_uri} />
              <div>
                <label htmlFor={`${uid}-cid`} className="block text-sm font-medium">Client ID</label>
                <input id={`${uid}-cid`} className="input w-full" autoComplete="off" value={clientId} onChange={(e) => setClientId(e.target.value)} />
                {err('google_client_id')}
              </div>
              <div>
                <label htmlFor={`${uid}-sec`} className="block text-sm font-medium">Client secret</label>
                <SecretInput id={`${uid}-sec`} value={secret} onChange={setSecret} />
                {err('google_client_secret')}
              </div>
            </>
          )}
        </section>

        <section className="space-y-3" aria-label="Allowed accounts step">
          <h2 className="text-lg font-semibold">Allowed Google accounts</h2>
          {status.emails_env_managed ? (
            <p role="note" className="text-sm text-slate-600 dark:text-slate-400">Allowed accounts are configured by the server environment (DOWNTUBE_ALLOWED_EMAILS).</p>
          ) : (
            <>
              <EmailChips label="Allowed accounts" emails={allowed} onChange={setAllowed} hint="Only these Google accounts can sign in. Press Enter or comma to add." />
              {err('allowed_emails')}
              <AdminPicker allowed={allowed} admins={admins} onChange={setAdminSel} />
              {err('admin_emails')}
            </>
          )}
        </section>

        {formError && <p role="alert" className="rounded-sm border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{formError}</p>}
        <button type="submit" className="btn btn-primary w-full py-2" disabled={m.isPending}>Complete setup</button>
        <p className="text-xs text-slate-500">DownTube is an independent self-hosted tool and is not affiliated with, endorsed by, or sponsored by Google or YouTube.</p>
      </form>
    </main>
  );
}
