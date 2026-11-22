import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { ApiError } from '../api/client';
import { endpoints } from '../api/endpoints';
import type { ArchiveMode, BulkResult, ChannelEstimate, ChannelRulePatch, ResolutionCap, SubscriptionOut } from '../api/types';
import { useMe } from '../auth';
import { ManagePermissionNote } from '../components/ManagePermission';
import { ErrorNote, Modal, PageHeader, Pagination, ResolutionNote, ResolutionSelect, Spinner, StateBadge, Thumb, useDebounced } from '../components/ui';
import { formatBytes, formatRelative } from '../lib/format';

export const MODES: { value: ArchiveMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'follow_new', label: 'Follow new uploads' },
  { value: 'backfill_once', label: 'Backfill existing once' },
  { value: 'backfill_and_follow', label: 'Backfill and follow' },
];

/** Turn a ResolutionCap into a patch fragment (null means "best available" -> clear_max_height). */
function resPatch(v: ResolutionCap): ChannelRulePatch {
  return v === null ? { clear_max_height: true } : { max_height: v };
}

interface PendingSingle { kind: 'single'; channel: SubscriptionOut; patch: ChannelRulePatch; estimate: ChannelEstimate }
interface PendingBulk { kind: 'bulk'; patch: ChannelRulePatch; estimates: ChannelEstimate[] }

function isEstimate(x: unknown): x is ChannelEstimate {
  return !!x && typeof x === 'object' && 'estimate_basis' in x && 'channel_id' in x;
}

function ConfirmBody({ e, title }: { e: ChannelEstimate; title?: string }) {
  return (
    <div className="rounded border border-slate-200 p-2 dark:border-slate-700">
      {title && <p className="font-medium">{title}</p>}
      <p>{e.video_count?.toLocaleString() ?? 'Unknown number of'} videos, estimated {e.estimated_bytes != null ? formatBytes(e.estimated_bytes) : 'size unknown'}.</p>
      <p className="text-xs text-slate-500">{e.estimate_basis}</p>
    </div>
  );
}

function FiltersDialog({ channel, onClose, onSave, error, pending }: {
  channel: SubscriptionOut; onClose: () => void; onSave: (p: ChannelRulePatch) => void; error: unknown; pending: boolean;
}) {
  const r = channel.rule;
  const [shorts, setShorts] = useState(r.include_shorts);
  const [live, setLive] = useState(r.include_live);
  const [after, setAfter] = useState(r.published_after ? r.published_after.slice(0, 10) : '');
  const [minutes, setMinutes] = useState(r.max_duration_s ? String(Math.round(r.max_duration_s / 60)) : '');
  const save = () => {
    const p: ChannelRulePatch = { include_shorts: shorts, include_live: live };
    if (after) p.published_after = `${after}T00:00:00Z`;
    if (minutes && Number(minutes) > 0) p.max_duration_s = Math.round(Number(minutes) * 60);
    onSave(p);
  };
  return (
    <Modal title={`Filters: ${channel.title}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={pending} onClick={save}>Save filters</button></>}>
      <label className="flex items-center gap-2"><input type="checkbox" checked={!shorts} onChange={(e) => setShorts(!e.target.checked)} /> Exclude Shorts</label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={!live} onChange={(e) => setLive(!e.target.checked)} /> Exclude livestreams</label>
      <div>
        <label htmlFor="f-after" className="block">Only videos published after</label>
        <input id="f-after" type="date" className="input" value={after} onChange={(e) => setAfter(e.target.value)} />
      </div>
      <div>
        <label htmlFor="f-dur" className="block">Maximum duration (minutes)</label>
        <input id="f-dur" type="number" min={1} className="input w-32" value={minutes} onChange={(e) => setMinutes(e.target.value)} placeholder="no limit" />
      </div>
      <p className="text-xs text-slate-500">Filters apply to videos discovered from now on. Date and duration limits can be changed but not cleared here.</p>
      <ErrorNote error={error} />
    </Modal>
  );
}

export function UnsubscribeDialog({ channel, onClose, onDone }: { channel: SubscriptionOut; onClose: () => void; onDone: () => void }) {
  const me = useMe();
  const m = useMutation({ mutationFn: () => endpoints.unsubscribe(channel.channel_id), onSuccess: onDone });
  return (
    <Modal title={`Unsubscribe from ${channel.title}?`} onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" disabled={!me.can_manage_youtube || m.isPending} onClick={() => m.mutate()}>Unsubscribe</button>
      </>}>
      <p>This unsubscribes your YouTube account from <strong>{channel.title}</strong>, turns archiving off for this channel and cancels its queued downloads.</p>
      <p>Videos already archived ({channel.archived_count}) stay in your library. Delete them from the Library page if you no longer want them.</p>
      {!me.can_manage_youtube && <ManagePermissionNote action="unsubscribe from channels" />}
      <ErrorNote error={m.error} />
    </Modal>
  );
}

export default function SubscriptionsPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('title');
  const [order, setOrder] = useState('asc');
  const [mode, setMode] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<PendingSingle | PendingBulk | null>(null);
  const [filtersFor, setFiltersFor] = useState<SubscriptionOut | null>(null);
  const [unsubFor, setUnsubFor] = useState<SubscriptionOut | null>(null);
  const [bulkMode, setBulkMode] = useState<ArchiveMode | ''>('');
  const [bulkRes, setBulkRes] = useState<ResolutionCap | 'keep'>('keep');
  const q = useDebounced(search);

  const list = useQuery({
    queryKey: ['subscriptions', q, sort, order, mode, status, page],
    queryFn: () => endpoints.subscriptions({ q, sort, order, mode, status, page, page_size: 25 }),
    placeholderData: (p) => p,
    refetchInterval: 10000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ['subscriptions'] });

  const patch = useMutation({
    mutationFn: ({ channel, p }: { channel: SubscriptionOut; p: ChannelRulePatch }) => endpoints.patchRule(channel.channel_id, p),
    onSuccess: () => { setPending(null); setFiltersFor(null); refresh(); },
    onError: async (e, { channel, p }) => {
      if (e instanceof ApiError && e.status === 409 && e.code === 'confirmation_required') {
        const nested = (e.details as { estimate?: unknown } | undefined)?.estimate;
        let est: ChannelEstimate | null = isEstimate(e.details) ? e.details : isEstimate(nested) ? nested : null;
        if (!est) {
          try { est = await endpoints.estimate(channel.channel_id); } catch { /* ignore */ }
        }
        est ??= { channel_id: channel.channel_id, video_count: channel.video_count, estimated_bytes: null, estimate_basis: e.message, is_large: true, requires_confirmation: true };
        setPending({ kind: 'single', channel, patch: p, estimate: est });
      }
    },
  });
  const bulk = useMutation({
    mutationFn: (b: { ids: string[]; p: ChannelRulePatch }) => endpoints.bulk({ channel_ids: b.ids, patch: b.p }),
    onSuccess: (r: BulkResult, b) => {
      refresh();
      if (r.needs_confirmation.length) setPending({ kind: 'bulk', patch: b.p, estimates: r.needs_confirmation });
      else { setPending(null); setSelected(new Set()); }
    },
  });
  const syncOne = useMutation({ mutationFn: (id: string) => endpoints.syncChannel(id), onSuccess: refresh });
  const syncAll = useMutation({ mutationFn: endpoints.syncSubscriptions, onSuccess: refresh });

  const items = list.data?.items ?? [];
  const allSelected = items.length > 0 && items.every((c) => selected.has(c.channel_id));
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleAll = () => setSelected((s) => {
    const n = new Set(s);
    if (allSelected) items.forEach((c) => n.delete(c.channel_id)); else items.forEach((c) => n.add(c.channel_id));
    return n;
  });

  const runBulk = (p: ChannelRulePatch) => bulk.mutate({ ids: [...selected], p });
  const applyBulk = () => {
    const p: ChannelRulePatch = {};
    if (bulkMode) p.mode = bulkMode;
    if (bulkRes !== 'keep') Object.assign(p, resPatch(bulkRes));
    if (Object.keys(p).length) runBulk(p);
  };

  return (
    <div>
      <PageHeader title="Subscriptions">
        <button className="btn" onClick={() => syncAll.mutate()} disabled={syncAll.isPending}>Refresh subscriptions</button>
      </PageHeader>
      <ResolutionNote />
      <div className="my-3 flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="sub-q">Search channels</label>
        <input id="sub-q" type="search" placeholder="Search channels" className="input min-w-[12rem] flex-1" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        <label className="sr-only" htmlFor="sub-mode">Filter by mode</label>
        <select id="sub-mode" className="input" value={mode} onChange={(e) => { setMode(e.target.value); setPage(1); }}>
          <option value="">All modes</option>
          {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        <label className="sr-only" htmlFor="sub-status">Filter by sync status</label>
        <select id="sub-status" className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All sync states</option>
          {['idle', 'running', 'ok', 'error', 'quota_deferred'].map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
        </select>
        <label className="sr-only" htmlFor="sub-sort">Sort by</label>
        <select id="sub-sort" className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="title">Title</option><option value="archived">Archived</option><option value="last_sync">Last sync</option><option value="video_count">Video count</option>
        </select>
        <label className="sr-only" htmlFor="sub-order">Order</label>
        <select id="sub-order" className="input" value={order} onChange={(e) => setOrder(e.target.value)}>
          <option value="asc">Ascending</option><option value="desc">Descending</option>
        </select>
      </div>

      {selected.size > 0 && (
        <div role="region" aria-label="Bulk actions" className="card sticky top-0 z-10 mb-3 flex flex-wrap items-end gap-2">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <div>
            <label htmlFor="bulk-mode" className="block text-xs">Mode</label>
            <select id="bulk-mode" className="input" value={bulkMode} onChange={(e) => setBulkMode(e.target.value as ArchiveMode | '')}>
              <option value="">Keep</option>{MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="bulk-res" className="block text-xs">Max resolution</label>
            <select id="bulk-res" className="input" value={bulkRes === 'keep' ? 'keep' : bulkRes ?? ''}
              onChange={(e) => setBulkRes(e.target.value === 'keep' ? 'keep' : e.target.value === '' ? null : (Number(e.target.value) as ResolutionCap))}>
              <option value="keep">Keep</option>
              <option value="">Best available (≤4K)</option><option value="2160">2160p 4K UHD</option><option value="1440">1440p</option>
              <option value="1080">1080p Full HD</option><option value="720">720p HD</option><option value="480">480p SD</option><option value="360">360p Low</option>
            </select>
          </div>
          <button className="btn btn-primary" onClick={applyBulk} disabled={bulk.isPending || (!bulkMode && bulkRes === 'keep')}>Apply</button>
          <button className="btn" onClick={() => runBulk({ paused: true })} disabled={bulk.isPending}>Pause</button>
          <button className="btn" onClick={() => runBulk({ paused: false })} disabled={bulk.isPending}>Resume</button>
          <button className="btn" onClick={() => setSelected(new Set())}>Clear selection</button>
          <ErrorNote error={bulk.error} />
        </div>
      )}

      {list.isLoading && <Spinner />}
      <ErrorNote error={list.error ?? syncOne.error} />
      {list.data && (
        <>
          <label className="mb-2 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} /> Select all on this page
          </label>
          <ul className="space-y-2">
            {items.map((c) => (
              <li key={c.channel_id} className="card p-3">
                <div className="flex flex-wrap items-start gap-3">
                  <input type="checkbox" className="mt-1" aria-label={`Select ${c.title}`} checked={selected.has(c.channel_id)} onChange={() => toggle(c.channel_id)} />
                  <Thumb src={c.thumbnail_url} className="h-12 w-12 rounded-full" />
                  <div className="min-w-0 flex-1 basis-56">
                    <p className="flex items-center gap-2 font-medium">
                      <span className="truncate">{c.title}</span>
                      {!c.subscribed && <span className="rounded bg-slate-200 px-1.5 text-xs font-normal text-slate-700 dark:bg-slate-700 dark:text-slate-200">Unsubscribed</span>}
                      <a href={c.url} target="_blank" rel="noreferrer" aria-label={`Open ${c.title} on YouTube`}><ExternalLink size={14} /></a>
                    </p>
                    <p className="line-clamp-2 text-xs text-slate-500">{c.description}</p>
                    <p className="text-xs text-slate-500">{c.video_count ?? '?'} videos · last success {formatRelative(c.last_success_at)}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="sr-only" htmlFor={`mode-${c.channel_id}`}>Mode for {c.title}</label>
                    <select id={`mode-${c.channel_id}`} className="input" value={c.rule.mode} onChange={(e) => patch.mutate({ channel: c, p: { mode: e.target.value as ArchiveMode } })}>
                      {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                    <ResolutionSelect label={`Max resolution for ${c.title}`} value={c.rule.max_height} onChange={(v) => patch.mutate({ channel: c, p: resPatch(v) })} />
                    <label className="flex items-center gap-1 text-sm">
                      <input type="checkbox" checked={c.rule.paused} onChange={(e) => patch.mutate({ channel: c, p: { paused: e.target.checked } })} /> Paused
                    </label>
                    <button className="btn" onClick={() => setFiltersFor(c)} aria-label={`Filters for ${c.title}`}>Filters</button>
                    <button className="btn" disabled={syncOne.isPending} onClick={() => syncOne.mutate(c.channel_id)} aria-label={`Sync ${c.title} now`}>Sync now</button>
                    {c.subscribed && <button className="btn btn-danger" onClick={() => setUnsubFor(c)} aria-label={`Unsubscribe from ${c.title}`}>Unsubscribe</button>}
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                  <StateBadge state={c.sync_status} />
                  <span>{c.queued_count} queued</span><span>{c.downloading_count} downloading</span>
                  <span>{c.archived_count} archived</span><span>{c.failed_count} failed</span>
                  {c.last_error && <span className="text-red-700 dark:text-red-400">{c.last_error}</span>}
                </div>
              </li>
            ))}
            {items.length === 0 && <li className="p-4 text-sm text-slate-500">No subscriptions match.</li>}
          </ul>
          <Pagination page={page} pageSize={list.data.page_size} total={list.data.total} onPage={setPage} />
        </>
      )}
      {patch.error && !(patch.error instanceof ApiError && patch.error.code === 'confirmation_required') && !filtersFor && <ErrorNote error={patch.error} />}

      {unsubFor && <UnsubscribeDialog channel={unsubFor} onClose={() => setUnsubFor(null)} onDone={() => { setUnsubFor(null); refresh(); }} />}

      {filtersFor && (
        <FiltersDialog channel={filtersFor} onClose={() => setFiltersFor(null)} pending={patch.isPending} error={patch.error}
          onSave={(p) => patch.mutate({ channel: filtersFor, p })} />
      )}

      {pending?.kind === 'single' && (
        <Modal title="Archive a large channel?" onClose={() => setPending(null)}
          footer={<>
            <button className="btn" onClick={() => setPending(null)}>Cancel</button>
            <button className="btn btn-primary" disabled={patch.isPending}
              onClick={() => patch.mutate({ channel: pending.channel, p: { ...pending.patch, confirm_large: true } })}>Confirm and start</button>
          </>}>
          <p><strong>{pending.channel.title}</strong> is large. Backfilling will queue many downloads.</p>
          <ConfirmBody e={pending.estimate} />
          <p>Make sure you have the rights to archive this content and enough free disk space.</p>
          <ErrorNote error={patch.error instanceof ApiError && patch.error.code === 'confirmation_required' ? null : patch.error} />
        </Modal>
      )}
      {pending?.kind === 'bulk' && (
        <Modal title="Large channels need confirmation" onClose={() => setPending(null)}
          footer={<>
            <button className="btn" onClick={() => setPending(null)}>Skip these</button>
            <button className="btn btn-primary" disabled={bulk.isPending}
              onClick={() => bulk.mutate({ ids: pending.estimates.map((e) => e.channel_id), p: { ...pending.patch, confirm_large: true } })}>Confirm all</button>
          </>}>
          <p>These channels were not changed because they are large. Confirm to apply the change and start archiving them.</p>
          {pending.estimates.map((e) => <ConfirmBody key={e.channel_id} e={e} title={items.find((c) => c.channel_id === e.channel_id)?.title ?? e.channel_id} />)}
        </Modal>
      )}
    </div>
  );
}
