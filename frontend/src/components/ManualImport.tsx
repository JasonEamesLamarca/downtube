import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, ExternalLink, FolderInput, Upload } from 'lucide-react';
import { endpoints } from '../api/endpoints';
import type { JobOut } from '../api/types';
import { formatBytes } from '../lib/format';
import { BROWSERS, SIGN_IN_ERRORS, type Browser, youtubeUrl, ytDlpCommand } from '../lib/ytdlp';
import { ErrorNote, Modal, ProgressBar } from './ui';

export function YouTubeLink({ videoId, title }: { videoId: string; title: string }) {
  return (
    <a className="btn" href={youtubeUrl(videoId)} target="_blank" rel="noopener noreferrer" aria-label={`Open ${title} on YouTube`}>
      <ExternalLink size={14} aria-hidden /> YouTube
    </a>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 2000); } catch { setDone(false); }
  };
  return <button type="button" className="btn" onClick={copy} aria-label={label}><Copy size={14} aria-hidden /> {done ? 'Copied' : 'Copy'}</button>;
}

/** Download a video with your own account (yt-dlp on your computer) and hand the file to DownTube. */
export function ImportDialog({ job, importPath, onClose }: { job: JobOut; importPath: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const name = job.title ?? job.video_id;
  const [browser, setBrowser] = useState<Browser>('chrome');
  const [file, setFile] = useState<File | null>(null);
  const [pct, setPct] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const cmd = ytDlpCommand(job.video_id, job.max_height, browser);
  const up = useMutation({
    mutationFn: (f: File) => {
      abort.current = new AbortController();
      setPct(0);
      return endpoints.uploadImport(job.id, f, setPct, abort.current.signal);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-counts'] });
      qc.invalidateQueries({ queryKey: ['library'] });
    },
  });
  const close = () => { abort.current?.abort(); onClose(); };

  return (
    <Modal title="Download it with your account" onClose={close} wide
      footer={<button className="btn" onClick={close}>{up.isSuccess ? 'Done' : 'Close'}</button>}>
      <p>
        {SIGN_IN_ERRORS.has(job.error_code ?? '')
          ? <>YouTube only serves <strong>{name}</strong> to a signed-in account. </>
          : <>If DownTube cannot download <strong>{name}</strong>, you can do it yourself. </>}
        DownTube never uses your YouTube cookies, so download it on your own computer, where you are signed
        in, and give DownTube the file.
      </p>
      <ol className="list-decimal space-y-3 pl-5">
        <li>
          <p>Open the video on YouTube and confirm you can watch it.</p>
          <p className="mt-1"><YouTubeLink videoId={job.video_id} title={name} /></p>
        </li>
        <li>
          <p>
            Run this <a className="underline" href="https://github.com/yt-dlp/yt-dlp#installation" target="_blank" rel="noopener noreferrer">yt-dlp</a> command
            on that computer (ffmpeg must be installed). It reads your browser's sign-in locally and keeps the original
            quality up to {job.max_height}p, without re-encoding.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label htmlFor="import-browser" className="text-sm">Browser</label>
            <select id="import-browser" className="input" value={browser} onChange={(e) => setBrowser(e.target.value as Browser)}>
              {BROWSERS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <CopyButton text={cmd} label="Copy the yt-dlp command" />
          </div>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded bg-slate-100 p-2 font-mono text-xs dark:bg-slate-900" aria-label="yt-dlp command">{cmd}</pre>
          <p className="mt-1 text-xs text-slate-500">
            On Windows, yt-dlp usually cannot read Chrome or Edge cookies; use Firefox there. Close the browser first if
            yt-dlp says the cookie database is locked.
          </p>
        </li>
        <li>
          <p>Upload the file here{importPath ? <>, or copy it into <code className="break-all">{importPath}</code> (picked up within 5 minutes)</> : null}.</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label htmlFor="import-file" className="sr-only">Video file</label>
            <input id="import-file" type="file" accept="video/mp4,video/x-matroska,video/webm,.mp4,.mkv,.webm,.m4v,.mov"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); up.reset(); }} />
            <button className="btn btn-primary" disabled={!file || up.isPending || up.isSuccess} onClick={() => file && up.mutate(file)}>
              <Upload size={14} aria-hidden /> Upload{file ? ` (${formatBytes(file.size)})` : ''}
            </button>
          </div>
          {up.isPending && (
            <div className="mt-2 space-y-1">
              <ProgressBar pct={pct} label={`Upload progress for ${name}`} />
              <p className="text-xs text-slate-500">{pct < 100 ? `Uploading… ${Math.round(pct)}%` : 'Checking the file…'}</p>
            </div>
          )}
          {up.isSuccess && <p role="status" className="mt-2 text-green-700 dark:text-green-400">Imported into the archive.</p>}
          <div className="mt-2"><ErrorNote error={up.error} /></div>
        </li>
      </ol>
      <p className="text-xs text-slate-500">
        DownTube checks the file before archiving it: it must be MP4 or MKV/WebM, last as long as the video, and not exceed {job.max_height}p.
      </p>
    </Modal>
  );
}

/** Files waiting in the import folder, with a manual scan. */
export function ImportFolderCard() {
  const qc = useQueryClient();
  const folder = useQuery({ queryKey: ['imports'], queryFn: endpoints.importFolder, refetchInterval: 30000 });
  const scan = useMutation({
    mutationFn: endpoints.scanImports,
    onSuccess: (data) => {
      qc.setQueryData(['imports'], { ...data, files: data.files.filter((f) => f.status !== 'imported') });
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-counts'] });
    },
  });
  const imported = scan.data?.files.filter((f) => f.status === 'imported').length ?? 0;
  const files = folder.data?.files ?? [];
  if (folder.isError) return null;
  return (
    <section className="card mb-4 space-y-2" aria-labelledby="import-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="import-h" className="flex items-center gap-2 font-semibold"><FolderInput size={16} aria-hidden /> Import folder</h2>
        <button className="btn" onClick={() => scan.mutate()} disabled={scan.isPending}>Scan now</button>
      </div>
      <p className="text-sm text-slate-500">
        Videos you downloaded yourself (for example age-restricted ones): copy them into{' '}
        <code className="break-all">{folder.data?.path ?? '…'}</code>. The file name must contain the video ID in
        brackets, e.g. <code>[dQw4w9WgXcQ]</code>. DownTube checks each file and moves it into the archive.
      </p>
      {scan.isSuccess && <p role="status" className="text-sm text-green-700 dark:text-green-400">Imported {imported} file{imported === 1 ? '' : 's'}.</p>}
      <ErrorNote error={scan.error} />
      {files.length > 0 && (
        <ul className="space-y-1 text-sm">
          {files.map((f) => (
            <li key={f.name} className="flex flex-wrap gap-x-2">
              <span className="break-all font-mono text-xs">{f.name}</span>
              <span className="text-slate-500">{formatBytes(f.size_bytes)}</span>
              <span className={f.status === 'failed' ? 'text-red-700 dark:text-red-400' : 'text-slate-500'}>
                {f.status === 'failed' ? `Not imported: ${f.message ?? 'unknown problem'}` : f.message ?? 'Waiting for the next scan'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
