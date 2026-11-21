import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../api/endpoints';
import type { JobOut, JobStatus, ResolutionCap } from '../api/types';
import { ErrorNote, PageHeader, Pagination, ProgressBar, ResolutionNote, ResolutionSelect, Spinner, StateBadge, Thumb } from '../components/ui';
import { extractVideoId, formatBytes, formatEta, formatRelative, formatSpeed } from '../lib/format';

type Tab = { key: string; label: string; status: JobStatus; counts: JobStatus[] };
const TABS: Tab[] = [
  { key: 'active', label: 'Active', status: 'running', counts: ['running'] },
  { key: 'queued', label: 'Queued', status: 'queued', counts: ['queued'] },
  { key: 'paused', label: 'Paused', status: 'paused', counts: ['paused'] },
  { key: 'completed', label: 'Completed', status: 'completed', counts: ['completed'] },
  { key: 'failed', label: 'Failed', status: 'failed', counts: ['failed'] },
  { key: 'cancelled', label: 'Cancelled', status: 'cancelled', counts: ['cancelled'] },
  { key: 'other', label: 'Other', status: 'unsupported', counts: ['unsupported', 'skipped'] },
];

function JobRow({ job, onAction, busy }: { job: JobOut; onAction: (a: 'retry' | 'cancel' | 'pause' | 'resume') => void; busy: boolean }) {
  const s = job.status;
  const can = {
    pause: s === 'running' || s === 'queued',
    resume: s === 'paused',
    cancel: s === 'running' || s === 'queued' || s === 'paused',
    retry: s === 'failed' || s === 'cancelled' || s === 'unsupported' || s === 'skipped',
  };
  const name = job.title ?? job.video_id;
  return (
    <li className="card flex flex-wrap items-center gap-3 p-3">
      <Thumb src={job.thumbnail_url} />
      <div className="min-w-0 flex-1 basis-64">
        <p className="truncate font-medium">{name}</p>
        <p className="truncate text-sm text-slate-500">{job.channel_title ?? '—'} · up to {job.max_height}p{job.kind === 'upgrade' ? ' · upgrade' : ''}</p>
        {s === 'running' && (
          <div className="mt-1 space-y-1">
            <ProgressBar pct={job.progress_pct} label={`Progress for ${name}`} />
            <p className="text-xs text-slate-500">
              {job.stage} · {Math.round(job.progress_pct ?? 0)}% · {formatBytes(job.bytes_downloaded)} / {formatBytes(job.bytes_total)} · {formatSpeed(job.speed_bps)} · ETA {formatEta(job.eta_s)}
            </p>
          </div>
        )}
        {s !== 'running' && s !== 'completed' && job.error_message && (
          <p className="mt-1 text-xs text-red-700 dark:text-red-400">{job.error_code ? `${job.error_code}: ` : ''}{job.error_message}</p>
        )}
        <p className="text-xs text-slate-500">
          Attempt {job.attempts}/{job.max_attempts}
          {job.next_attempt_at && s === 'queued' && <> · next try {formatRelative(job.next_attempt_at)}</>}
        </p>
      </div>
      <StateBadge state={s} />
      <div className="flex gap-1">
        {can.pause && <button className="btn" disabled={busy} onClick={() => onAction('pause')} aria-label={`Pause ${name}`}>Pause</button>}
        {can.resume && <button className="btn" disabled={busy} onClick={() => onAction('resume')} aria-label={`Resume ${name}`}>Resume</button>}
        {can.retry && <button className="btn" disabled={busy} onClick={() => onAction('retry')} aria-label={`Retry ${name}`}>Retry</button>}
        {can.cancel && <button className="btn btn-danger" disabled={busy} onClick={() => onAction('cancel')} aria-label={`Cancel ${name}`}>Cancel</button>}
      </div>
    </li>
  );
}

export default function DownloadsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState(TABS[0].key);
  const [otherStatus, setOtherStatus] = useState<JobStatus>('unsupported');
  const [page, setPage] = useState(1);
  const [input, setInput] = useState('');
  const [maxH, setMaxH] = useState<ResolutionCap>(null);
  const [inputErr, setInputErr] = useState<string | null>(null);
  const t = TABS.find((x) => x.key === tab)!;
  const status = t.key === 'other' ? otherStatus : t.status;

  const counts = useQuery({ queryKey: ['download-counts'], queryFn: endpoints.downloadCounts, refetchInterval: 2000 });
  const anyActive = (counts.data?.running ?? 0) > 0;
  const interval = anyActive ? 2000 : 10000;
  const list = useQuery({
    queryKey: ['downloads', status, page],
    queryFn: () => endpoints.downloads({ status, page, page_size: 25 }),
    refetchInterval: interval,
    placeholderData: (p) => p,
  });
  const refresh = () => { qc.invalidateQueries({ queryKey: ['downloads'] }); qc.invalidateQueries({ queryKey: ['download-counts'] }); };
  const act = useMutation({ mutationFn: ({ id, a }: { id: number; a: 'retry' | 'cancel' | 'pause' | 'resume' }) => endpoints.jobAction(id, a), onSuccess: refresh });
  const add = useMutation({ mutationFn: endpoints.enqueue, onSuccess: () => { setInput(''); refresh(); } });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = extractVideoId(input);
    if (!id) { setInputErr('Could not find an 11-character YouTube video ID in that text.'); return; }
    setInputErr(null);
    add.mutate({ video_id: id, max_height: maxH });
  };
  const countFor = (x: Tab) => x.counts.reduce((n, k) => n + (counts.data?.[k] ?? 0), 0);

  return (
    <div>
      <PageHeader title="Downloads" />
      <form onSubmit={submit} className="card mb-4 space-y-2" aria-labelledby="manual-h">
        <h2 id="manual-h" className="font-semibold">Archive a video by ID or URL</h2>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex-1 basis-64">
            <label htmlFor="manual-id" className="block text-sm">Video ID or URL</label>
            <input id="manual-id" className="input w-full" value={input} onChange={(e) => setInput(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
          </div>
          <div>
            <label htmlFor="manual-res" className="block text-sm">Max resolution</label>
            <ResolutionSelect id="manual-res" label="Max resolution" value={maxH} onChange={setMaxH} />
          </div>
          <button className="btn btn-primary" type="submit" disabled={add.isPending}>Archive</button>
        </div>
        <ResolutionNote />
        {inputErr && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{inputErr}</p>}
        <ErrorNote error={add.error} />
        {add.isSuccess && <p role="status" className="text-sm text-green-700 dark:text-green-400">Added to the queue.</p>}
      </form>

      <div role="tablist" aria-label="Download status" className="mb-3 flex flex-wrap gap-1">
        {TABS.map((x) => (
          <button key={x.key} role="tab" aria-selected={tab === x.key} className={`btn ${tab === x.key ? 'btn-primary' : ''}`}
            onClick={() => { setTab(x.key); setPage(1); }}>
            {x.label} <span className="badge">{countFor(x)}</span>
          </button>
        ))}
      </div>
      {tab === 'other' && (
        <div className="mb-2">
          <label htmlFor="other-status" className="mr-2 text-sm">Show</label>
          <select id="other-status" className="input" value={otherStatus} onChange={(e) => { setOtherStatus(e.target.value as JobStatus); setPage(1); }}>
            <option value="unsupported">Unsupported</option><option value="skipped">Skipped</option>
          </select>
        </div>
      )}
      <div role="tabpanel" aria-live={anyActive ? 'polite' : 'off'}>
        {list.isLoading && <Spinner />}
        <ErrorNote error={list.error ?? act.error} />
        {list.data && (
          <>
            <ul className="space-y-2">
              {list.data.items.map((j) => (
                <JobRow key={j.id} job={j} busy={act.isPending} onAction={(a) => act.mutate({ id: j.id, a })} />
              ))}
              {list.data.items.length === 0 && <li className="p-4 text-sm text-slate-500">Nothing here.</li>}
            </ul>
            <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
          </>
        )}
      </div>
    </div>
  );
}
