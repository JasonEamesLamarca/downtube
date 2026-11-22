import { useId, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../api/endpoints';
import type { SettingsOut, SettingsUpdate } from '../api/types';
import { useMe, ME_KEY } from '../auth';
import { ErrorNote, Modal, PageHeader, ProgressBar, ResolutionNote, ResolutionSelect, Spinner } from '../components/ui';
import { formatBytes } from '../lib/format';
import { fieldErrors } from '../lib/errors';
import { GoogleSection } from '../components/GoogleSettings';
import { MANAGE_LOGIN_URL } from '../components/ManagePermission';

export function diffSettings(orig: SettingsOut, cur: SettingsOut): SettingsUpdate {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(cur) as (keyof SettingsOut)[]) {
    if (k === 'archive_root') continue; // changed only via /storage/root
    if (cur[k] !== orig[k]) out[k] = cur[k];
  }
  // Wire conventions: 0 = unlimited bandwidth, "" = download window always.
  if ('bandwidth_limit_kbps' in out && out.bandwidth_limit_kbps === null) out.bandwidth_limit_kbps = 0;
  if ('download_window' in out && !out.download_window) out.download_window = '';
  return out as SettingsUpdate;
}

const WINDOW_RE = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card space-y-3" aria-label={title}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string; children: ReactNode; htmlFor: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium">{label}</label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
      {error && <p role="alert" className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </div>
  );
}

function FolderPicker({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [path, setPath] = useState<string | undefined>(undefined);
  const [newName, setNewName] = useState('');
  const br = useQuery({ queryKey: ['browse', path], queryFn: () => endpoints.browse(path) });
  const set = useMutation({ mutationFn: endpoints.setRoot, onSuccess: onDone });
  const cur = br.data?.path;
  return (
    <Modal title="Choose archive folder" onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!cur || set.isPending} onClick={() => cur && set.mutate({ path: cur, create: false })}>Use this folder</button>
      </>}>
      <p role="note" className="rounded-sm border border-amber-400 bg-amber-50 p-2 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
        Changing the archive folder does not move existing archives. Files already downloaded stay where they are and will no longer appear in new-download checks for the new location.
      </p>
      {br.isLoading && <Spinner />}
      <ErrorNote error={br.error ?? set.error} />
      {br.data && (
        <>
          <p className="break-all text-xs">Current location: <code>{br.data.path}</code></p>
          <ul className="max-h-60 divide-y divide-slate-200 overflow-y-auto rounded-sm border border-slate-200 dark:divide-slate-700 dark:border-slate-700">
            {br.data.parent && <li><button className="w-full p-2 text-left hover:bg-slate-100 dark:hover:bg-slate-800" onClick={() => setPath(br.data.parent!)}>.. (up)</button></li>}
            {br.data.entries.map((e) => (
              <li key={e.path}>
                <button className="w-full p-2 text-left hover:bg-slate-100 dark:hover:bg-slate-800" onClick={() => setPath(e.path)}>
                  {e.name}{!e.writable && ' (read-only)'}
                </button>
              </li>
            ))}
            {br.data.entries.length === 0 && <li className="p-2 text-slate-500">No subfolders.</li>}
          </ul>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label htmlFor="new-folder" className="block text-sm">New subfolder</label>
              <input id="new-folder" className="input w-full" value={newName} onChange={(e) => setNewName(e.target.value)} />
            </div>
            <button className="btn" disabled={!newName.trim() || set.isPending} onClick={() => set.mutate({ path: `${br.data.path.replace(/\/$/, '')}/${newName.trim()}`, create: true })}>Create and use</button>
          </div>
        </>
      )}
    </Modal>
  );
}

function DisconnectDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [revoke, setRevoke] = useState(true);
  const [del, setDel] = useState(false);
  const m = useMutation({
    mutationFn: () => endpoints.disconnect({ revoke_google_token: revoke, delete_local_data: del }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ME_KEY }),
  });
  return (
    <Modal title="Disconnect Google account" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-danger" disabled={m.isPending} onClick={() => m.mutate()}>Disconnect</button></>}>
      <p>Syncing will stop. Your archived media files are never deleted by DownTube.</p>
      <label className="flex items-center gap-2"><input type="checkbox" checked={revoke} onChange={(e) => setRevoke(e.target.checked)} /> Revoke DownTube's access at Google</label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={del} onChange={(e) => setDel(e.target.checked)} /> Delete my local DownTube data (database records only)</label>
      <ErrorNote error={m.error} />
    </Modal>
  );
}

function LogsViewer() {
  const [on, setOn] = useState(false);
  const [lines, setLines] = useState(200);
  const q = useQuery({ queryKey: ['logs', lines], queryFn: () => endpoints.logs(lines), enabled: on });
  return (
    <Section title="Logs">
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="log-lines" className="block text-sm">Lines</label>
          <select id="log-lines" className="input" value={lines} onChange={(e) => setLines(Number(e.target.value))}>
            {[100, 200, 500, 1000].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <button className="btn" onClick={() => (on ? q.refetch() : setOn(true))}>{on ? 'Refresh' : 'Show logs'}</button>
      </div>
      <ErrorNote error={q.error} />
      {q.data && <pre aria-label="Log output" className="max-h-96 overflow-auto rounded-sm bg-slate-900 p-2 text-xs text-slate-100">{q.data.lines.join('\n') || '(empty)'}</pre>}
    </Section>
  );
}

function SidecarBackfill() {
  const m = useMutation({ mutationFn: () => endpoints.writeSidecars(false) });
  return (
    <div className="text-sm">
      <button className="btn" disabled={m.isPending} onClick={() => m.mutate()}>Write .nfo files for videos already archived</button>
      {m.isPending && <Spinner label="Writing description files…" />}
      {m.data && <p role="status" className="mt-1">Wrote {m.data.written} file(s); {m.data.skipped} already had one{m.data.failed ? `; ${m.data.failed} failed (see logs)` : ''}.</p>}
      <ErrorNote error={m.error} />
    </div>
  );
}

export default function SettingsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ['settings'], queryFn: endpoints.settings });
  const storage = useQuery({ queryKey: ['storage'], queryFn: endpoints.storage });
  const [edits, setEdits] = useState<Partial<SettingsOut>>({});
  const [picker, setPicker] = useState(false);
  const [disc, setDisc] = useState(false);
  const [saved, setSaved] = useState(false);
  const form: SettingsOut | null = s.data ? { ...s.data, ...edits } : null;
  const uid = useId();

  const changes = (s.data && form ? diffSettings(s.data, form) : {});
  const dirty = Object.keys(changes).length > 0;
  const save = useMutation({
    mutationFn: () => endpoints.updateSettings(changes),
    onSuccess: (r) => { qc.setQueryData(['settings'], r); setEdits({}); setSaved(true); },
  });
  const errs = fieldErrors(save.error);
  const winErr = form?.download_window && !WINDOW_RE.test(form.download_window) ? 'Use HH:MM-HH:MM, e.g. 01:00-07:00.' : undefined;

  if (s.isLoading || !form) return s.error ? <ErrorNote error={s.error} /> : <Spinner />;
  const set = <K extends keyof SettingsOut>(k: K, v: SettingsOut[K]) => { setSaved(false); setEdits({ ...edits, [k]: v }); };
  const num = (k: keyof SettingsOut, label: string, opts: { min?: number; max?: number; hint?: string } = {}) => (
    <Field label={label} htmlFor={`${uid}-${k}`} hint={opts.hint} error={errs[k]}>
      <input id={`${uid}-${k}`} type="number" className="input block w-40" min={opts.min} max={opts.max}
        value={form[k] as number} onChange={(e) => set(k, (e.target.value === '' ? 0 : Number(e.target.value)) as never)} />
    </Field>
  );
  const check = (k: keyof SettingsOut, label: string, hint?: string) => (
    <div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form[k] as boolean} onChange={(e) => set(k, e.target.checked as never)} /> {label}</label>
      {hint && <p className="ml-6 text-xs text-slate-500">{hint}</p>}
      {errs[k] && <p role="alert" className="text-xs text-red-700">{errs[k]}</p>}
    </div>
  );
  const st = storage.data;

  return (
    <div>
      <PageHeader title="Settings">
        <button className="btn btn-primary" disabled={!dirty || !!winErr || save.isPending} onClick={() => save.mutate()}>Save changes</button>
      </PageHeader>
      <div aria-live="polite">
        {saved && <p role="status" className="mb-2 text-sm text-green-700 dark:text-green-400">Settings saved.</p>}
        {save.error && Object.keys(errs).length === 0 && <ErrorNote error={save.error} />}
        {Object.keys(errs).length > 0 && <p role="alert" className="mb-2 text-sm text-red-700 dark:text-red-400">Some values were rejected. See the highlighted fields.</p>}
      </div>
      <div className="space-y-4">
        <Section title="Account & connection">
          <p className="text-sm">Signed in as <strong>{me.email}</strong> ({me.account_status.replace(/_/g, ' ')}).</p>
          <div className="flex flex-wrap gap-2">
            {me.account_status !== 'active' && <a className="btn btn-primary" href="/api/auth/login">Connect Google account</a>}
            <button className="btn" onClick={async () => { await endpoints.logout(); qc.setQueryData(ME_KEY, null); }}>Sign out</button>
            <button className="btn btn-danger" onClick={() => setDisc(true)}>Disconnect Google account…</button>
          </div>
          <div className="text-sm">
            <p className="font-medium">Permission to manage your YouTube account</p>
            {me.can_manage_youtube ? (
              <p>Granted. DownTube uses it only when you unsubscribe from a channel or remove a like while deleting a video. You can withdraw it in your Google account under Security → Third-party connections.</p>
            ) : (
              <>
                <p>Not granted. DownTube only reads your account. Grant it to unsubscribe from channels and remove likes from DownTube.</p>
                <a className="btn mt-1" href={MANAGE_LOGIN_URL}>Grant permission with Google</a>
              </>
            )}
          </div>
        </Section>

        <Section title="Storage">
          <p className="text-sm">Archive folder: <code>{form.archive_root}</code></p>
          {st && (
            <>
              {st.host_path_hint && <p className="text-sm">On the host: <code>{st.host_path_hint}</code></p>}
              <ProgressBar pct={st.total_bytes ? ((st.total_bytes - st.free_bytes) / st.total_bytes) * 100 : 0} label="Disk used" />
              <p className="text-sm">{formatBytes(st.free_bytes)} free of {formatBytes(st.total_bytes)}; archive uses {formatBytes(st.archive_bytes)}.</p>
              {st.below_threshold && <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">Free space is below your safety threshold.</p>}
              {!st.writable && <p role="alert" className="text-sm text-red-700 dark:text-red-400">This folder is not writable.</p>}
            </>
          )}
          <p className="text-xs text-slate-500">Changing the folder does not move existing archives.</p>
          <button className="btn" onClick={() => setPicker(true)}>Change folder…</button>
        </Section>

        <Section title="Likes monitoring">
          <Field label="Liked videos mode" htmlFor={`${uid}-likes_mode`} error={errs.likes_mode} hint="Future only records a baseline of current likes and archives new ones. All also archives your existing likes.">
            <select id={`${uid}-likes_mode`} className="input block" value={form.likes_mode} onChange={(e) => set('likes_mode', e.target.value as SettingsOut['likes_mode'])}>
              <option value="off">Off</option><option value="future_only">Future likes only</option><option value="all">All likes</option>
            </select>
          </Field>
          <Field label="Quality for liked videos" htmlFor={`${uid}-likes_max_height`} error={errs.likes_max_height} hint="Overrides channel limits for videos you liked.">
            <ResolutionSelect id={`${uid}-likes_max_height`} label="Quality for liked videos" value={form.likes_max_height} onChange={(v) => set('likes_max_height', v)} />
          </Field>
          <ResolutionNote />
          {num('likes_poll_minutes', 'Check for new likes every (minutes)', { min: 5, max: 1440 })}
          {num('likes_full_reconcile_hours', 'Full reconciliation every (hours)', { min: 1, max: 720 })}
          {num('subscriptions_refresh_hours', 'Refresh subscriptions every (hours)', { min: 1, max: 720 })}
          {num('channel_poll_minutes', 'Check monitored channels every (minutes)', { min: 15, max: 10080 })}
        </Section>

        <Section title="Quality & formats">
          <Field label="Default resolution limit for channels" htmlFor={`${uid}-default_max_height`} error={errs.default_max_height}>
            <ResolutionSelect id={`${uid}-default_max_height`} label="Default resolution limit" value={form.default_max_height} onChange={(v) => set('default_max_height', v)} />
          </Field>
          <ResolutionNote />
          <Field label="Preferred audio codec" htmlFor={`${uid}-audio_preference`} error={errs.audio_preference}>
            <select id={`${uid}-audio_preference`} className="input block" value={form.audio_preference} onChange={(e) => set('audio_preference', e.target.value as 'aac' | 'opus')}>
              <option value="aac">AAC</option><option value="opus">Opus</option>
            </select>
          </Field>
          {check('prefer_hdr', 'Prefer HDR streams when available at the chosen resolution')}
          {check('compatibility_profile', 'Compatibility profile: prefer H.264/AAC (good for Direct Play) at the same resolution', 'Never lowers resolution to gain compatibility.')}
          {check('mkv_fallback', 'Allow MKV when no MP4-compatible streams exist at the best resolution')}
          {check('keep_superseded_copies', 'Keep the old file (renamed .superseded) when a higher quality version replaces it')}
        </Section>

        <Section title="Downloads">
          {num('max_concurrent_downloads', 'Concurrent downloads', { min: 1, max: 8 })}
          <Field label="Bandwidth limit (KB/s, empty for unlimited)" htmlFor={`${uid}-bw`} error={errs.bandwidth_limit_kbps} hint="Minimum 64 KB/s.">
            <input id={`${uid}-bw`} type="number" min={64} className="input block w-40" value={form.bandwidth_limit_kbps ?? ''}
              onChange={(e) => set('bandwidth_limit_kbps', e.target.value === '' ? null : Number(e.target.value))} />
          </Field>
          <Field label="Download window (HH:MM-HH:MM, empty for always)" htmlFor={`${uid}-win`} error={winErr ?? errs.download_window} hint="Local time in the configured timezone.">
            <input id={`${uid}-win`} className="input block w-48" placeholder="01:00-07:00" value={form.download_window ?? ''} onChange={(e) => set('download_window', e.target.value)} />
          </Field>
          <Field label="Timezone" htmlFor={`${uid}-tz`} error={errs.timezone}>
            <input id={`${uid}-tz`} className="input block w-60" value={form.timezone} onChange={(e) => set('timezone', e.target.value)} />
          </Field>
        </Section>

        <Section title="Disk safety">
          {num('min_free_gb', 'Keep at least this much free space (GB)', { min: 1, hint: 'Downloads wait when free space would drop below this.' })}
          {num('large_channel_threshold', 'Large channel threshold (videos)', { min: 1, hint: 'Backfilling channels above this requires confirmation.' })}
        </Section>

        <Section title="Media server files (Plex, Jellyfin, Kodi)">
          {check('write_thumbnails', 'Write thumbnail (.jpg) next to each video')}
          {check('write_nfo', 'Write the title and YouTube description (.nfo) next to each video', 'Read by Plex (Movie libraries with the Plex NFO agent), Jellyfin, Emby and Kodi.')}
          {check('embed_metadata', 'Store the title, description and date inside new MP4 files', 'Plex reads these tags in any library type. The video and audio are copied, never re-encoded.')}
          <SidecarBackfill />
        </Section>

        <Section title="API quota">
          {num('quota_daily_budget', 'Daily YouTube API quota budget (units)', { min: 100, max: 1000000, hint: 'Google gives 10,000 units per day by default.' })}
        </Section>

        {me.is_admin && <GoogleSection />}

        {me.is_admin && <LogsViewer />}
      </div>

      {picker && <FolderPicker onClose={() => setPicker(false)} onDone={() => { setPicker(false); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['storage'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); }} />}
      {disc && <DisconnectDialog onClose={() => setDisc(false)} />}
    </div>
  );
}
