import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { endpoints } from '../api/endpoints';
import { ErrorNote, PageHeader, ProgressBar, Spinner, StateBadge } from '../components/ui';
import { formatBytes, formatDateTime, formatRelative, formatSpeed } from '../lib/format';

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold">{value}</dd>
    </div>
  );
}

export default function DashboardPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['dashboard'], queryFn: endpoints.dashboard, refetchInterval: 5000 });
  const sync = useMutation({
    mutationFn: () => endpoints.syncLikes(false),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dashboard'] }),
  });
  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <ErrorNote error={q.error ?? new Error('No data')} />;
  const d = q.data;
  const used = d.storage.total_bytes - d.storage.free_bytes;
  const usedPct = d.storage.total_bytes ? (used / d.storage.total_bytes) * 100 : 0;
  const quotaPct = d.quota.budget ? (d.quota.used / d.quota.budget) * 100 : 0;
  return (
    <div>
      <PageHeader title="Dashboard">
        <button className="btn btn-primary" onClick={() => sync.mutate()} disabled={sync.isPending}>Synchronize now</button>
      </PageHeader>
      <ErrorNote error={sync.error} />
      {sync.isSuccess && <p role="status" className="mb-2 text-sm text-green-700 dark:text-green-400">Synchronization started.</p>}
      <p className="mb-4 text-sm text-slate-600 dark:text-slate-400">
        Account: {d.account_email ?? 'not connected'} ({d.account_status.replace(/_/g, ' ')}). Likes mode: {d.likes_mode.replace(/_/g, ' ')}.
        Last likes sync: {formatRelative(d.last_likes_sync)}.
      </p>
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Archived videos" value={d.archived_videos} />
        <Stat label="Archive size" value={formatBytes(d.archive_bytes)} />
        <Stat label="Monitored channels" value={d.monitored_channels} />
        <Stat label="Liked discovered" value={d.liked_videos_discovered} />
        <Stat label="Active downloads" value={d.downloads_active} />
        <Stat label="Pending downloads" value={d.downloads_pending} />
        <Stat label="Failed downloads" value={d.downloads_failed} />
        <Stat label="Current speed" value={formatSpeed(d.current_speed_bps)} />
      </dl>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <section className="card space-y-2" aria-labelledby="storage-h">
          <h2 id="storage-h" className="font-semibold">Storage</h2>
          <ProgressBar pct={usedPct} label="Disk used" />
          <p className="text-sm">{formatBytes(d.storage.free_bytes)} free of {formatBytes(d.storage.total_bytes)} at <code>{d.storage.root_path}</code>
            {d.storage.host_path_hint && <> (host: <code>{d.storage.host_path_hint}</code>)</>}</p>
          {d.storage.below_threshold && (
            <p role="alert" className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400">
              <AlertTriangle size={16} aria-hidden /> Free space is below the safety threshold of {formatBytes(d.storage.min_free_bytes)}. New downloads are paused.
            </p>
          )}
          {!d.storage.writable && <p role="alert" className="text-sm text-red-700 dark:text-red-400">The archive folder is not writable.</p>}
        </section>
        <section className="card space-y-2" aria-labelledby="quota-h">
          <h2 id="quota-h" className="font-semibold">YouTube API quota</h2>
          <ProgressBar pct={quotaPct} label="Quota used" />
          <p className="text-sm">{d.quota.used.toLocaleString()} of {d.quota.budget.toLocaleString()} units used on {d.quota.day}. Resets {formatRelative(d.quota.resets_at)}.</p>
          {d.quota.exhausted && <p role="alert" className="text-sm text-amber-700 dark:text-amber-400">Quota exhausted. Syncs are deferred until it resets.</p>}
        </section>
      </div>
      <section className="card mt-4" aria-labelledby="sync-h">
        <h2 id="sync-h" className="mb-2 font-semibold">Recent sync activity</h2>
        {d.recent_syncs.length === 0 ? <p className="text-sm text-slate-500">No syncs yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr><th className="p-1">Kind</th><th className="p-1">Status</th><th className="p-1">Started</th><th className="p-1">Seen</th><th className="p-1">New</th><th className="p-1">Queued</th><th className="p-1">Note</th></tr></thead>
              <tbody>
                {d.recent_syncs.map((s) => (
                  <tr key={s.id} className="border-t border-slate-200 dark:border-slate-800">
                    <td className="p-1">{s.kind}{s.target ? ` (${s.target})` : ''}</td>
                    <td className="p-1"><StateBadge state={s.status} /></td>
                    <td className="p-1" title={formatDateTime(s.started_at)}>{formatRelative(s.started_at)}</td>
                    <td className="p-1">{s.items_seen}</td><td className="p-1">{s.items_new}</td><td className="p-1">{s.jobs_queued}</td>
                    <td className="p-1 text-red-700 dark:text-red-400">{s.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
