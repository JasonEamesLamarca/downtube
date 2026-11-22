import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { endpoints } from '../api/endpoints';
import type { GoogleSettingsOut, GoogleSettingsUpdate } from '../api/types';
import { fieldErrors } from '../lib/errors';
import { ErrorNote, Spinner } from './ui';
import { AdminPicker, CopyField, EmailChips, GoogleInstructions, SecretInput } from './GoogleConfig';

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

export function diffGoogle(orig: GoogleSettingsOut, cur: { clientId: string; secret: string; allowed: string[]; admins: string[] }): GoogleSettingsUpdate {
  const out: GoogleSettingsUpdate = {};
  if (!orig.google_env_managed) {
    if (cur.clientId.trim() !== (orig.google_client_id ?? '')) out.google_client_id = cur.clientId.trim();
    if (cur.secret) out.google_client_secret = cur.secret;
  }
  if (!orig.emails_env_managed) {
    if (!sameSet(cur.allowed, orig.allowed_emails)) out.allowed_emails = cur.allowed;
    if (!sameSet(cur.admins, orig.admin_emails)) out.admin_emails = cur.admins;
  }
  return out;
}

export function GoogleSection() {
  const q = useQuery({ queryKey: ['admin-google'], queryFn: endpoints.getGoogleSettings });
  return (
    <section className="card space-y-3" aria-label="Google sign-in and access">
      <h2 className="text-lg font-semibold">Google sign-in &amp; access</h2>
      {q.isLoading && <Spinner />}
      <ErrorNote error={q.error} />
      {q.data && <GoogleForm data={q.data} />}
    </section>
  );
}

function GoogleForm({ data }: { data: GoogleSettingsOut }) {
  const uid = useId();
  const qc = useQueryClient();
  const [clientId, setClientId] = useState(data.google_client_id ?? '');
  const [secret, setSecret] = useState('');
  const [allowed, setAllowed] = useState(data.allowed_emails);
  const [adminSel, setAdminSel] = useState(data.admin_emails);
  const admins = adminSel.filter((a) => allowed.includes(a));
  const [saved, setSaved] = useState(false);
  const changes = diffGoogle(data, { clientId, secret, allowed, admins });
  const dirty = Object.keys(changes).length > 0;
  const save = useMutation({
    mutationFn: () => endpoints.updateGoogleSettings(changes),
    onSuccess: (r) => {
      qc.setQueryData(['admin-google'], r);
      setClientId(r.google_client_id ?? ''); setSecret(''); setAllowed(r.allowed_emails); setAdminSel(r.admin_emails);
      setSaved(true);
    },
  });
  const errs = fieldErrors(save.error);
  const selfErr = save.error instanceof ApiError && save.error.status === 409 && save.error.code === 'cannot_remove_self';
  const touch = () => setSaved(false);

  return (
    <>
      {data.google_env_managed ? (
        <p role="note" className="text-sm text-slate-600 dark:text-slate-400">Google sign-in client is configured by the server environment (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) and cannot be changed here.</p>
      ) : (
        <>
          <GoogleInstructions redirectUri={data.redirect_uri} />
          <div>
            <label htmlFor={`${uid}-cid`} className="block text-sm font-medium">Client ID</label>
            <input id={`${uid}-cid`} className="input w-full" autoComplete="off" value={clientId} onChange={(e) => { touch(); setClientId(e.target.value); }} />
            {errs.google_client_id && <p role="alert" className="text-xs text-red-700">{errs.google_client_id}</p>}
            <p className="text-xs text-slate-500">After changing the client ID you will eventually need to sign in again.</p>
          </div>
          <div>
            <p className="text-sm">Client secret: <strong>{data.google_client_secret_set ? 'Set' : 'Not set'}</strong></p>
            <label htmlFor={`${uid}-sec`} className="block text-sm font-medium">Replace secret</label>
            <SecretInput id={`${uid}-sec`} value={secret} onChange={(v) => { touch(); setSecret(v); }} placeholder="Leave blank to keep unchanged" />
            {errs.google_client_secret && <p role="alert" className="text-xs text-red-700">{errs.google_client_secret}</p>}
          </div>
        </>
      )}
      <CopyField label="Authorized redirect URI" value={data.redirect_uri} />
      {data.emails_env_managed ? (
        <div role="note" className="text-sm text-slate-600 dark:text-slate-400">
          <p>Allowed accounts are configured by the server environment (DOWNTUBE_ALLOWED_EMAILS) and are read-only here.</p>
          <p className="mt-1">Allowed: {data.allowed_emails.join(', ') || '(none)'}</p>
          <p>Administrators: {data.admin_emails.join(', ') || '(none)'}</p>
        </div>
      ) : (
        <>
          <EmailChips label="Allowed Google accounts" emails={allowed} onChange={(e) => { touch(); setAllowed(e); }} />
          {errs.allowed_emails && <p role="alert" className="text-xs text-red-700">{errs.allowed_emails}</p>}
          <AdminPicker allowed={allowed} admins={admins} onChange={(a) => { touch(); setAdminSel(a); }} />
          {errs.admin_emails && <p role="alert" className="text-xs text-red-700">{errs.admin_emails}</p>}
        </>
      )}
      <div aria-live="polite">
        {saved && <p role="status" className="text-sm text-green-700 dark:text-green-400">Google settings saved.</p>}
        {selfErr && <p role="alert" className="text-sm text-red-700 dark:text-red-400">You can't remove your own account</p>}
        {!selfErr && Object.keys(errs).length === 0 && <ErrorNote error={save.error} />}
      </div>
      <div className="pt-3">
        <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save Google settings</button>
      </div>
    </>
  );
}
