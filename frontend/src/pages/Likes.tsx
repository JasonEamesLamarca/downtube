import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { endpoints } from '../api/endpoints';
import type { ArchiveState } from '../api/types';
import { ErrorNote, Modal, PageHeader, Pagination, Spinner, StateBadge, Thumb, useDebounced } from '../components/ui';
import { formatDate, heightLabel } from '../lib/format';

const STATES: ArchiveState[] = ['not_archived', 'queued', 'downloading', 'archived', 'failed', 'unsupported', 'excluded'];

export default function LikesPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [state, setState] = useState('');
  const [page, setPage] = useState(1);
  const [confirm, setConfirm] = useState(false);
  const q = useDebounced(search);
  const list = useQuery({
    queryKey: ['likes', q, state, page],
    queryFn: () => endpoints.likes({ q, state, page, page_size: 25 }),
    placeholderData: (p) => p,
  });
  const done = () => qc.invalidateQueries({ queryKey: ['likes'] });
  const sync = useMutation({ mutationFn: (full: boolean) => endpoints.syncLikes(full), onSuccess: done });
  const backfill = useMutation({ mutationFn: () => endpoints.backfillLikes(true), onSuccess: () => { setConfirm(false); done(); } });
  return (
    <div>
      <PageHeader title="Liked videos">
        <button className="btn" onClick={() => sync.mutate(false)} disabled={sync.isPending}>Rescan likes</button>
        <button className="btn" onClick={() => sync.mutate(true)} disabled={sync.isPending}>Full reconciliation</button>
        <button className="btn btn-primary" onClick={() => setConfirm(true)}>Archive historical likes</button>
      </PageHeader>
      <ErrorNote error={sync.error} />
      {sync.isSuccess && <p role="status" className="mb-2 text-sm text-green-700 dark:text-green-400">Scan started.</p>}
      {backfill.isSuccess && <p role="status" className="mb-2 text-sm text-green-700 dark:text-green-400">Queued {backfill.data.queued} video(s).</p>}
      <p className="mb-3 text-xs text-slate-500">YouTube only exposes your newest 1000 likes through its API.</p>
      <div className="mb-3 flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="likes-q">Search liked videos</label>
        <input id="likes-q" type="search" placeholder="Search title or channel" className="input min-w-[12rem] flex-1" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        <label className="sr-only" htmlFor="likes-state">Archive state</label>
        <select id="likes-state" className="input" value={state} onChange={(e) => { setState(e.target.value); setPage(1); }}>
          <option value="">All states</option>
          {STATES.map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
        </select>
      </div>
      {list.isLoading && <Spinner />}
      <ErrorNote error={list.error} />
      {list.data && (
        <>
          <ul className="space-y-2">
            {list.data.items.map((v) => (
              <li key={v.video_id} className="card flex flex-wrap items-center gap-3 p-3">
                <Thumb src={v.thumbnail_url} />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{v.title ?? v.video_id}</p>
                  <p className="truncate text-sm text-slate-500">{v.channel_title ?? 'Unknown channel'}</p>
                </div>
                <dl className="grid grid-cols-3 gap-x-4 text-xs">
                  <div><dt className="text-slate-500">Source quality</dt><dd>{heightLabel(v.best_available_height)}</dd></div>
                  <div><dt className="text-slate-500">Archived</dt><dd>{heightLabel(v.archived_height)}</dd></div>
                  <div><dt className="text-slate-500">Discovered</dt><dd>{formatDate(v.discovered_at)}</dd></div>
                </dl>
                <StateBadge state={v.archive_state} />
                <a className="btn px-2" href={v.url} target="_blank" rel="noreferrer" aria-label={`Open ${v.title ?? v.video_id} on YouTube`}><ExternalLink size={14} /></a>
              </li>
            ))}
            {list.data.items.length === 0 && <li className="p-4 text-sm text-slate-500">No liked videos match.</li>}
          </ul>
          <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
        </>
      )}
      {confirm && (
        <Modal title="Archive historical likes?" onClose={() => setConfirm(false)}
          footer={<>
            <button className="btn" onClick={() => setConfirm(false)}>Cancel</button>
            <button className="btn btn-primary" disabled={backfill.isPending} onClick={() => backfill.mutate()}>Queue all</button>
          </>}>
          <p>This queues every known liked video that has not been archived yet. This can be a lot of data. Make sure you have the rights to archive them and enough free disk space.</p>
          <ErrorNote error={backfill.error} />
        </Modal>
      )}
    </div>
  );
}
