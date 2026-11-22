import { api, uploadFile } from './client';
import type * as T from './types';

type Q = Record<string, string | number | boolean | null | undefined>;

export const endpoints = {
  me: () => api<T.Me>('/auth/me', { noAuthRedirect: true }),
  acknowledge: () => api<T.Me>('/auth/acknowledge', { method: 'POST' }),
  logout: () => api<void>('/auth/logout', { method: 'POST' }),
  disconnect: (b: T.DisconnectRequest) => api<void>('/auth/disconnect', { method: 'POST', body: b }),
  dashboard: () => api<T.Dashboard>('/dashboard'),
  subscriptions: (q: Q) => api<T.Page<T.SubscriptionOut>>('/subscriptions', { query: q }),
  syncSubscriptions: () => api<T.SyncRunOut>('/subscriptions/sync', { method: 'POST' }),
  syncChannel: (id: string) => api<T.SyncRunOut>(`/subscriptions/${id}/sync`, { method: 'POST' }),
  estimate: (id: string) => api<T.ChannelEstimate>(`/subscriptions/${id}/estimate`),
  patchRule: (id: string, p: T.ChannelRulePatch) =>
    api<T.SubscriptionOut>(`/subscriptions/${id}/rule`, { method: 'PATCH', body: p }),
  unsubscribe: (id: string) => api<T.SubscriptionOut>(`/subscriptions/${id}/unsubscribe`, { method: 'POST' }),
  bulk: (b: T.BulkRuleRequest) => api<T.BulkResult>('/subscriptions/bulk', { method: 'POST', body: b }),
  likes: (q: Q) => api<T.Page<T.VideoOut>>('/likes', { query: q }),
  syncLikes: (full: boolean) => api<T.SyncRunOut>('/likes/sync', { method: 'POST', body: { full } }),
  backfillLikes: (confirm: boolean) => api<{ queued: number }>('/likes/backfill', { method: 'POST', body: { confirm } }),
  downloads: (q: Q) => api<T.Page<T.JobOut>>('/downloads', { query: q }),
  downloadCounts: () => api<T.JobCounts>('/downloads/counts'),
  enqueue: (b: T.ManualEnqueueRequest) => api<T.JobOut>('/downloads', { method: 'POST', body: b }),
  jobAction: (id: number, a: 'retry' | 'cancel' | 'pause' | 'resume') =>
    api<T.JobOut>(`/downloads/${id}/${a}`, { method: 'POST' }),
  library: (q: Q) => api<T.Page<T.MediaFileOut>>('/library', { query: q }),
  deleteVideo: (id: string, b: T.DeleteVideoRequest) =>
    api<T.DeleteVideoResult>(`/library/${id}/delete`, { method: 'POST', body: b }),
  importFolder: () => api<T.ImportFolderOut>('/imports'),
  scanImports: () => api<T.ImportFolderOut>('/imports/scan', { method: 'POST' }),
  uploadImport: (jobId: number, file: File, onProgress?: (pct: number) => void, signal?: AbortSignal) =>
    uploadFile<T.JobOut>(`/downloads/${jobId}/import`, file, { query: { filename: file.name }, onProgress, signal }),
  writeSidecars: (overwrite = false) => api<T.SidecarResult>('/library/sidecars', { method: 'POST', query: { overwrite } }),
  settings: () => api<T.SettingsOut>('/settings'),
  updateSettings: (u: T.SettingsUpdate) => api<T.SettingsOut>('/settings', { method: 'PUT', body: u }),
  storage: () => api<T.StorageInfo>('/storage'),
  browse: (path?: string) => api<T.BrowseResult>('/storage/browse', { query: { path } }),
  setRoot: (b: T.SetRootRequest) => api<T.StorageInfo>('/storage/root', { method: 'POST', body: b }),
  setupStatus: () => api<T.SetupStatus>('/setup/status'),
  submitSetup: (b: T.SetupRequest) => api<T.SetupStatus>('/setup', { method: 'POST', body: b, noAuthRedirect: true }),
  getGoogleSettings: () => api<T.GoogleSettingsOut>('/admin/google'),
  updateGoogleSettings: (b: T.GoogleSettingsUpdate) => api<T.GoogleSettingsOut>('/admin/google', { method: 'PUT', body: b }),
  logs: (lines = 200) => api<T.LogLines>('/logs', { query: { lines } }),
};
