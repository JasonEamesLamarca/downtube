import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, LayoutGrid, List } from 'lucide-react';
import { endpoints } from '../api/endpoints';
import type { MediaFileOut } from '../api/types';
import { Drawer, ErrorNote, PageHeader, Pagination, Spinner, Thumb, useDebounced } from '../components/ui';
import { formatBytes, formatDate, formatDuration, heightLabel } from '../lib/format';

export default function LibraryPage() {
  const [search, setSearch] = useState('');
  const [channel, setChannel] = useState('');
  const [sort, setSort] = useState('date');
  const [order, setOrder] = useState('desc');
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<MediaFileOut | null>(null);
  const q = useDebounced(search);
  const list = useQuery({
    queryKey: ['library', q, channel, sort, order, page],
    queryFn: () => endpoints.library({ q, channel_id: channel, sort, order, page, page_size: 24 }),
    placeholderData: (p) => p,
  });
  const channels = useQuery({ queryKey: ['library-channels'], queryFn: () => endpoints.library({ page_size: 100, sort: 'title' }), staleTime: 60_000 });
  const channelOpts = new Map<string, string>();
  (channels.data?.items ?? []).forEach((m) => m.channel_id && channelOpts.set(m.channel_id, m.channel_title ?? m.channel_id));

  return (
    <div>
      <PageHeader title="Library">
        <div role="group" aria-label="View mode" className="flex gap-1">
          <button className="btn" aria-pressed={view === 'grid'} onClick={() => setView('grid')}><LayoutGrid size={16} aria-hidden /> Grid</button>
          <button className="btn" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={16} aria-hidden /> List</button>
        </div>
      </PageHeader>
      <div className="mb-3 flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="lib-q">Search library</label>
        <input id="lib-q" type="search" placeholder="Search title or channel" className="input min-w-[12rem] flex-1" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        <label className="sr-only" htmlFor="lib-ch">Channel</label>
        <select id="lib-ch" className="input" value={channel} onChange={(e) => { setChannel(e.target.value); setPage(1); }}>
          <option value="">All channels</option>
          {[...channelOpts].map(([id, t]) => <option key={id} value={id}>{t}</option>)}
        </select>
        <label className="sr-only" htmlFor="lib-sort">Sort by</label>
        <select id="lib-sort" className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="date">Date downloaded</option><option value="title">Title</option><option value="size">Size</option><option value="height">Resolution</option>
        </select>
        <label className="sr-only" htmlFor="lib-order">Order</label>
        <select id="lib-order" className="input" value={order} onChange={(e) => setOrder(e.target.value)}>
          <option value="desc">Descending</option><option value="asc">Ascending</option>
        </select>
      </div>
      {list.isLoading && <Spinner />}
      <ErrorNote error={list.error} />
      {list.data && (
        <>
          <ul className={view === 'grid' ? 'grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4' : 'space-y-2'}>
            {list.data.items.map((m) => (
              <li key={m.id} className={`card p-3 ${view === 'list' ? 'flex items-center gap-3' : ''}`}>
                <button className="block w-full text-left" onClick={() => setSel(m)} aria-label={`Details for ${m.title ?? m.video_id}`}>
                  <Thumb src={m.thumbnail_url} className={view === 'grid' ? 'aspect-video w-full' : 'h-12 w-20'} />
                </button>
                <div className="mt-2 min-w-0 flex-1 text-sm">
                  <p className="truncate font-medium">{m.title ?? m.video_id}</p>
                  <p className="truncate text-slate-500">{m.channel_title}</p>
                  <p className="text-xs text-slate-500">{heightLabel(m.height)}{m.is_hdr ? ' HDR' : ''} · {m.vcodec ?? '?'} · {m.container} · {formatBytes(m.size_bytes)} · {formatDate(m.created_at)}</p>
                  <p className="truncate text-xs text-slate-400" title={m.rel_path}>{m.rel_path}</p>
                </div>
                <a className="btn px-2" href={m.url} target="_blank" rel="noreferrer" aria-label={`Open ${m.title ?? m.video_id} on YouTube`}><ExternalLink size={14} /></a>
              </li>
            ))}
            {list.data.items.length === 0 && <li className="p-4 text-sm text-slate-500">Nothing archived yet.</li>}
          </ul>
          <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
        </>
      )}
      {sel && (
        <Drawer title={sel.title ?? sel.video_id} onClose={() => setSel(null)}>
          <video key={sel.video_id} controls preload="metadata" className="w-full rounded bg-black" src={`/api/library/${sel.video_id}/stream`} />
          <p className="mt-2 text-xs text-slate-500">Some codecs (for example AV1, VP9 in MP4, or HEVC) or MKV files may not play in every browser. The file itself is unchanged; use a media player or Plex if playback fails.</p>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div><dt className="text-slate-500">Channel</dt><dd>{sel.channel_title ?? '—'}</dd></div>
            <div><dt className="text-slate-500">Resolution</dt><dd>{sel.width}×{sel.height}{sel.fps ? ` @ ${sel.fps}fps` : ''}</dd></div>
            <div><dt className="text-slate-500">Codecs</dt><dd>{sel.vcodec ?? '?'} / {sel.acodec ?? '?'}</dd></div>
            <div><dt className="text-slate-500">Container</dt><dd>{sel.container}</dd></div>
            <div><dt className="text-slate-500">Duration</dt><dd>{formatDuration(sel.duration_s)}</dd></div>
            <div><dt className="text-slate-500">Size</dt><dd>{formatBytes(sel.size_bytes)}</dd></div>
            <div className="col-span-2"><dt className="text-slate-500">Path</dt><dd className="break-all">{sel.rel_path}</dd></div>
          </dl>
          <a className="btn mt-3" href={sel.url} target="_blank" rel="noreferrer">Open on YouTube</a>
        </Drawer>
      )}
    </div>
  );
}
