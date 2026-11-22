import { useId, useState } from 'react';
import { Copy, Eye, EyeOff, X } from 'lucide-react';

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

/** Split free text into normalised (trimmed, lowercased) tokens. */
export function parseEmails(text: string): { valid: string[]; invalid: string[] } {
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const t of text.split(/[\s,;]+/).map((x) => x.trim().toLowerCase()).filter(Boolean)) {
    (EMAIL_RE.test(t) ? valid : invalid).push(t);
  }
  return { valid, invalid };
}

export const dedupe = (a: string[]): string[] => [...new Set(a)];

export function CopyField({ label, value }: { label: string; value: string }) {
  const id = useId();
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">{label}</label>
      <div className="flex gap-2">
        <input id={id} readOnly className="input flex-1 font-mono text-xs" value={value} onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className="btn" onClick={async () => {
          try { await navigator.clipboard.writeText(value); setCopied(true); } catch { /* ignore */ }
        }}><Copy size={14} /> {copied ? 'Copied' : 'Copy'}</button>
      </div>
    </div>
  );
}

export function SecretInput({ id, value, onChange, placeholder }: { id: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="flex gap-2">
      <input id={id} type={show ? 'text' : 'password'} autoComplete="off" className="input flex-1" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      <button type="button" className="btn" aria-label={show ? 'Hide secret' : 'Show secret'} aria-pressed={show} onClick={() => setShow(!show)}>
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

export function EmailChips({ label, emails, onChange, hint }: { label: string; emails: string[]; onChange: (e: string[]) => void; hint?: string }) {
  const id = useId();
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const commit = (raw: string) => {
    const { valid, invalid } = parseEmails(raw);
    if (valid.length) onChange(dedupe([...emails, ...valid]));
    setError(invalid.length ? `Not a valid email address: ${invalid.join(', ')}` : '');
    setText(invalid.join(' '));
  };
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium">{label}</label>
      {emails.length > 0 && (
        <ul className="my-1 flex flex-wrap gap-1">
          {emails.map((e) => (
            <li key={e} className="flex items-center gap-1 rounded-full bg-slate-200 px-2 py-0.5 text-xs dark:bg-slate-700">
              {e}
              <button type="button" aria-label={`Remove ${e}`} onClick={() => onChange(emails.filter((x) => x !== e))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input id={id} type="text" className="input flex-1" value={text} placeholder="name@gmail.com"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(text); } }}
          onBlur={() => { if (text.trim()) commit(text); }} />
        <button type="button" className="btn" onClick={() => commit(text)}>Add</button>
      </div>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </div>
  );
}

/** Admins are a subset of allowed emails, chosen with checkboxes. */
export function AdminPicker({ allowed, admins, onChange }: { allowed: string[]; admins: string[]; onChange: (a: string[]) => void }) {
  return (
    <fieldset>
      <legend className="text-sm font-medium">Administrators</legend>
      <p className="text-xs text-slate-500">Administrators can change Google sign-in, allowed accounts and other server settings, and view logs.</p>
      {allowed.length === 0 && <p className="text-xs text-slate-500">Add an allowed account first.</p>}
      {allowed.map((e) => (
        <label key={e} className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={admins.includes(e)}
            onChange={(ev) => onChange(ev.target.checked ? dedupe([...admins, e]) : admins.filter((x) => x !== e))} />
          {e}
        </label>
      ))}
    </fieldset>
  );
}

export function GoogleInstructions({ redirectUri }: { redirectUri: string }) {
  return (
    <details className="rounded-sm border border-slate-300 p-2 text-sm dark:border-slate-700">
      <summary className="cursor-pointer font-medium">How to create a Google sign-in client</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        <li>Create a project in the Google Cloud Console.</li>
        <li>Enable the <strong>YouTube Data API v3</strong> for that project.</li>
        <li>Configure the OAuth consent screen: user type <strong>External</strong>; scopes <code>openid</code>, <code>email</code>, <code>profile</code>, <code>youtube.readonly</code>. Set Publishing status to <strong>In production</strong>; in Testing, sign-in expires every 7 days.</li>
        <li>Create an OAuth client ID of type <strong>Web application</strong>.</li>
        <li>Add this Authorized redirect URI: <code className="break-all">{redirectUri}</code></li>
        <li>Copy the client ID and client secret here.</li>
      </ol>
    </details>
  );
}
