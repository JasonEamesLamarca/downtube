import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { RESOLUTION_NOTE, RESOLUTION_OPTIONS, parseResolution } from '../lib/format';
import type { ResolutionCap } from '../api/types';

export function Modal({ title, onClose, children, footer, dismissible = true, wide = false }: {
  title: string; onClose?: () => void; children: ReactNode; footer?: ReactNode; dismissible?: boolean; wide?: boolean;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dismissible) onClose?.();
      if (e.key === 'Tab' && ref.current) {
        const f = ref.current.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.(); };
  }, [onClose, dismissible]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        className={`card max-h-[90vh] w-full overflow-y-auto ${wide ? 'max-w-3xl' : 'max-w-lg'}`}>
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
          {dismissible && onClose && (
            <button className="btn px-1.5" aria-label="Close dialog" onClick={onClose}><X size={16} /></button>
          )}
        </div>
        <div className="space-y-3 text-sm">{children}</div>
        {footer && <div className="mt-4 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <aside ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        className="h-full w-full max-w-xl overflow-y-auto bg-white p-4 shadow-xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
          <button className="btn px-1.5" aria-label="Close details" onClick={onClose}><X size={16} /></button>
        </div>
        {children}
      </aside>
    </div>
  );
}

export function ProgressBar({ pct, label }: { pct: number | null | undefined; label: string }) {
  const v = Math.max(0, Math.min(100, pct ?? 0));
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v)}
      className="h-2 w-full overflow-hidden rounded-sm bg-slate-200 dark:bg-slate-700">
      <div className="h-full bg-brand-500 transition-all" style={{ width: `${v}%` }} />
    </div>
  );
}

export function ResolutionSelect({ value, onChange, label, id, disabled, className = '' }: {
  value: ResolutionCap; onChange: (v: ResolutionCap) => void; label: string; id?: string; disabled?: boolean; className?: string;
}) {
  return (
    <select id={id} aria-label={id ? undefined : label} className={`input ${className}`} disabled={disabled}
      value={value ?? ''} onChange={(e) => onChange(parseResolution(e.target.value))}>
      {RESOLUTION_OPTIONS.map((o) => <option key={String(o.value)} value={o.value ?? ''}>{o.label}</option>)}
    </select>
  );
}

export function ResolutionNote() {
  return <p className="text-xs text-slate-600 dark:text-slate-400">{RESOLUTION_NOTE}</p>;
}

export function Pagination({ page, pageSize, total, onPage }: {
  page: number; pageSize: number; total: number; onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 py-2 text-sm">
      <span>{total} item{total === 1 ? '' : 's'}</span>
      <div className="flex items-center gap-2">
        <button className="btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span aria-live="polite">Page {page} of {pages}</span>
        <button className="btn" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </nav>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <p role="alert" className="rounded-sm border border-red-300 bg-red-50 p-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{msg}</p>;
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <p role="status" className="p-4 text-sm text-slate-500">{label}</p>;
}

export function Thumb({ src, alt = '', className = 'h-12 w-20' }: { src: string | null | undefined; alt?: string; className?: string }) {
  // Only add the default corner radius when the caller sets none (e.g. round channel avatars).
  const radius = /\brounded-/.test(className) ? '' : 'rounded-sm';
  return src
    ? <img src={src} alt={alt} loading="lazy" className={`${className} ${radius} shrink-0 bg-slate-200 object-cover dark:bg-slate-800`} />
    : <div aria-hidden className={`${className} ${radius} shrink-0 bg-slate-200 dark:bg-slate-800`} />;
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-2xl font-bold">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

const STATE_COLORS: Record<string, string> = {
  archived: 'bg-green-100 text-green-900 dark:bg-green-900 dark:text-green-100',
  completed: 'bg-green-100 text-green-900 dark:bg-green-900 dark:text-green-100',
  ok: 'bg-green-100 text-green-900 dark:bg-green-900 dark:text-green-100',
  downloading: 'bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100',
  running: 'bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100',
  queued: 'bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100',
  failed: 'bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100',
  error: 'bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100',
  unsupported: 'bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100',
  quota_deferred: 'bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100',
};
export function StateBadge({ state }: { state: string }) {
  return <span className={`badge ${STATE_COLORS[state] ?? ''}`}>{state.replace(/_/g, ' ')}</span>;
}
