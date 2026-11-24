import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, LayoutGrid, List, Trash2 } from 'lucide-react';
import { endpoints } from '../api/endpoints';
import type { DeleteVideoResult, MediaFileOut } from '../api/types';
import { useMe } from '../auth';
import { ManagePermissionNote } from '../components/ManagePermission';
import { Drawer, ErrorNote, Modal, PageHeader, Pagination, Spinner, Thumb, useDebounced } from '../components/ui';
import { formatBytes, formatDate, formatDuration, heightLabel } from '../lib/format';

export function DeleteVideoDialog({ item, onClose, onDone }: {
  item: MediaFileOut; onClose: () => void; onDone: (r: DeleteVideoResult) => void;
}) {
  const me = useMe();
  const liked = item.sources.includes('like');
  const fromChannel = item.sources.includes('subscription');
  const [removeLike, setRemoveLike] = useState(liked && me.can_manage_youtube);
  const del = useMutation({
    mutationFn: () => endpoints.deleteVideo(item.video_id, { remove_like: liked && removeLike }),
    onSuccess: onDone,
  });
  return (
    <Modal title="Delete from library?" onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" disabled={del.isPending} onClick={() => del.mutate()}>Delete video</button>
      </>}>
      <p><strong>{item.title ?? item.video_id}</strong> will be deleted from disk, together with its thumbnail, description file and any kept older copy. This cannot be undone.</p>
      <p>DownTube will not download it again{fromChannel ? ', even though its channel is archived' : ''}. You can still download it again later from the Downloads page.</p>
      {liked && (
        <>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={removeLike} disabled={!me.can_manage_youtube}
              onChange={(e) => setRemoveLike(e.target.checked)} />
            <span>Also remove the like on YouTube</span>
          </label>
          {!me.can_manage_youtube && <ManagePermissionNote action="remove the like on YouTube" />}
        </>
      )}
      <ErrorNote error={del.error} />
    </Modal>
  );
}

export default function LibraryPage() {
  const [search, setSearch] = useState('');
  const [channel, setChannel] = useState('');
  const [sort, setSort] = useState('published');
  const [order, setOrder] = useState('desc');
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<MediaFileOut | null>(null);
  const [toDelete, setToDelete] = useState<MediaFileOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const qc = useQueryClient();
  const q = useDebounced(search);
  const list = useQuery({
    queryKey: ['library', q, channel, sort, order, page],
    queryFn: () => endpoints.library({ q, channel_id: channel, sort, order, page, page_size: 24 }),
    placeholderData: (p) => p,
  });
  const channels = useQuery({ queryKey: ['library-channels'], queryFn: endpoints.libraryChannels, staleTime: 60_000 });

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
          {(channels.data ?? []).map((c) => <option key={c.channel_id} value={c.channel_id}>{c.title} ({c.count})</option>)}
        </select>
        <label className="sr-only" htmlFor="lib-sort">Sort by</label>
        <select id="lib-sort" className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="published">Release date</option><option value="downloaded">Date downloaded</option><option value="title">Title</option><option value="size">Size</option><option value="height">Resolution</option>
        </select>
        <label className="sr-only" htmlFor="lib-order">Order</label>
        <select id="lib-order" className="input" value={order} onChange={(e) => setOrder(e.target.value)}>
          <option value="desc">Descending</option><option value="asc">Ascending</option>
        </select>
      </div>
      {notice && <p role="status" className="mb-2 text-sm text-green-700 dark:text-green-400">{notice}</p>}
      {list.isLoading && <Spinner />}
      <ErrorNote error={list.error} />
      {list.data && (
        <>
          <ul className={view === 'grid' ? 'grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4' : 'space-y-2'}>
            {list.data.items.map((m) => (
              <li key={m.id} className={`card p-3 ${view === 'list' ? 'flex items-center gap-3' : ''}`}>
                <button className={view === 'grid' ? 'block w-full text-left' : 'shrink-0'} onClick={() => setSel(m)} aria-label={`Details for ${m.title ?? m.video_id}`}>
                  <Thumb src={m.thumbnail_url} className={view === 'grid' ? 'aspect-video w-full' : 'h-16 w-28'} />
                </button>
                <div className={`min-w-0 text-sm ${view === 'grid' ? 'mt-2' : 'flex-1'}`}>
                  <p className="truncate font-medium">{m.title ?? m.video_id}</p>
                  <p className="truncate text-slate-500">{m.channel_title}</p>
                  <p className="text-xs text-slate-500">{heightLabel(Math.min(m.width, m.height))}{m.is_hdr ? ' HDR' : ''} · {m.vcodec ?? '?'} · {m.container} · {formatBytes(m.size_bytes)}</p>
                  <p className="text-xs text-slate-500"><span title="Released on YouTube">Released {formatDate(m.published_at)}</span> · <span title="Archived by DownTube">archived {formatDate(m.created_at)}</span></p>
                  <p className="truncate text-xs text-slate-400" title={m.rel_path}>{m.rel_path}</p>
                </div>
                <div className={`flex gap-1 ${view === 'grid' ? 'mt-2' : 'shrink-0'}`}>
                  <a className="btn px-2" href={m.url} target="_blank" rel="noreferrer" aria-label={`Open ${m.title ?? m.video_id} on YouTube`}><ExternalLink size={14} /></a>
                  <button className="btn px-2" onClick={() => setToDelete(m)} aria-label={`Delete ${m.title ?? m.video_id}`}><Trash2 size={14} /></button>
                </div>
              </li>
            ))}
            {list.data.items.length === 0 && <li className="p-4 text-sm text-slate-500">Nothing archived yet.</li>}
          </ul>
          <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
        </>
      )}
      {sel && (
        <Drawer title={sel.title ?? sel.video_id} onClose={() => setSel(null)}>
          <video key={sel.video_id} controls preload="metadata" className="w-full rounded-sm bg-black" src={`/api/library/${sel.video_id}/stream`} />
          <p className="mt-2 text-xs text-slate-500">Some codecs (for example AV1, VP9 in MP4, or HEVC) or MKV files may not play in every browser. The file itself is unchanged; use a media player or Plex if playback fails.</p>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div><dt className="text-slate-500">Channel</dt><dd>{sel.channel_title ?? '—'}</dd></div>
            <div><dt className="text-slate-500">Released</dt><dd>{formatDate(sel.published_at)}</dd></div>
            <div><dt className="text-slate-500">Archived</dt><dd>{formatDate(sel.created_at)}</dd></div>
            <div><dt className="text-slate-500">Resolution</dt><dd>{sel.width}×{sel.height}{sel.fps ? ` @ ${sel.fps}fps` : ''}</dd></div>
            <div><dt className="text-slate-500">Codecs</dt><dd>{sel.vcodec ?? '?'} / {sel.acodec ?? '?'}</dd></div>
            <div><dt className="text-slate-500">Container</dt><dd>{sel.container}</dd></div>
            <div><dt className="text-slate-500">Duration</dt><dd>{formatDuration(sel.duration_s)}</dd></div>
            <div><dt className="text-slate-500">Size</dt><dd>{formatBytes(sel.size_bytes)}</dd></div>
            <div className="col-span-2"><dt className="text-slate-500">Path</dt><dd className="break-all">{sel.rel_path}</dd></div>
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            <a className="btn" href={sel.url} target="_blank" rel="noreferrer">Open on YouTube</a>
            <button className="btn btn-danger" onClick={() => setToDelete(sel)}><Trash2 size={14} aria-hidden /> Delete…</button>
          </div>
        </Drawer>
      )}
      {toDelete && (
        <DeleteVideoDialog item={toDelete} onClose={() => setToDelete(null)} onDone={(r) => {
          setToDelete(null);
          setSel(null);
          const parts = [`Deleted ${r.deleted_files.length} file${r.deleted_files.length === 1 ? '' : 's'}`];
          if (r.like_removed) parts.push('removed the like on YouTube');
          setNotice(`${parts.join(' and ')}. ${r.warnings.join(' ')}`.trim());
          qc.invalidateQueries({ queryKey: ['library'] });
          qc.invalidateQueries({ queryKey: ['library-channels'] });
          qc.invalidateQueries({ queryKey: ['dashboard'] });
        }} />
      )}
    </div>
  );
}
