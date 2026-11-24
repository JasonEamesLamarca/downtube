// Mirrors backend/downtube/schemas.py. Change both together. Datetimes are ISO-8601 UTC strings.

export type ResolutionCap = 2160 | 1440 | 1080 | 720 | 480 | 360 | null;
export type ArchiveMode = 'off' | 'backfill_once' | 'backfill_and_follow' | 'follow_new';
export type LikesMode = 'off' | 'future_only' | 'all';
export type JobStatus =
  | 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled' | 'unsupported' | 'skipped';
export type JobStage = 'pending' | 'probing' | 'downloading' | 'merging' | 'validating' | 'finalizing' | 'done';
export type SyncStatus = 'idle' | 'running' | 'ok' | 'error' | 'quota_deferred';
export type Source = 'like' | 'subscription' | 'manual';
export type ArchiveState =
  | 'not_archived' | 'queued' | 'downloading' | 'archived' | 'failed' | 'unsupported' | 'excluded';

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface ErrorBody { code: string; message: string; details?: unknown }

export interface Me {
  id: number;
  email: string;
  display_name: string | null;
  picture_url: string | null;
  is_admin: boolean;
  acknowledged_terms: boolean;
  account_status: 'active' | 'reauth_required' | 'disconnected';
  youtube_channel_id: string | null;
  /** The optional permission to manage the YouTube account (unsubscribe, remove likes) was granted. */
  can_manage_youtube: boolean;
  csrf_token: string;
}

export interface DisconnectRequest { revoke_google_token: boolean; delete_local_data: boolean }

export interface StorageInfo {
  root_path: string;
  host_path_hint: string | null;
  total_bytes: number;
  free_bytes: number;
  archive_bytes: number;
  min_free_bytes: number;
  below_threshold: boolean;
  writable: boolean;
}

export interface QuotaInfo { day: string; used: number; budget: number; exhausted: boolean; resets_at: string }

export interface SyncRunOut {
  id: number;
  kind: string;
  target: string | null;
  trigger: string;
  status: SyncStatus;
  started_at: string;
  finished_at: string | null;
  items_seen: number;
  items_new: number;
  jobs_queued: number;
  quota_used: number;
  error: string | null;
}

export interface Dashboard {
  account_email: string | null;
  account_status: string;
  archived_videos: number;
  archive_bytes: number;
  monitored_channels: number;
  liked_videos_discovered: number;
  downloads_active: number;
  downloads_pending: number;
  downloads_failed: number;
  current_speed_bps: number;
  storage: StorageInfo;
  quota: QuotaInfo;
  likes_mode: LikesMode;
  last_likes_sync: string | null;
  recent_syncs: SyncRunOut[];
}

export interface ChannelRuleOut {
  mode: ArchiveMode;
  paused: boolean;
  max_height: ResolutionCap;
  include_shorts: boolean;
  include_live: boolean;
  published_after: string | null;
  max_duration_s: number | null;
  activated_at: string | null;
  backfill_completed_at: string | null;
}

export interface ChannelRulePatch {
  mode?: ArchiveMode | null;
  paused?: boolean | null;
  max_height?: ResolutionCap;
  clear_max_height?: boolean;
  include_shorts?: boolean | null;
  include_live?: boolean | null;
  published_after?: string | null;
  max_duration_s?: number | null;
  confirm_large?: boolean;
}

export interface BulkRuleRequest { channel_ids: string[]; patch: ChannelRulePatch }

export interface SubscriptionOut {
  channel_id: string;
  title: string;
  description: string | null;
  thumbnail_url: string | null;
  url: string;
  video_count: number | null;
  subscribed: boolean;
  rule: ChannelRuleOut;
  sync_status: SyncStatus;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  archived_count: number;
  queued_count: number;
  downloading_count: number;
  failed_count: number;
}

export interface ChannelEstimate {
  channel_id: string;
  video_count: number | null;
  estimated_bytes: number | null;
  estimate_basis: string;
  is_large: boolean;
  requires_confirmation: boolean;
}

export interface BulkResult { updated: string[]; needs_confirmation: ChannelEstimate[]; errors: Record<string, string> }

export interface VideoOut {
  video_id: string;
  title: string | null;
  channel_id: string | null;
  channel_title: string | null;
  thumbnail_url: string | null;
  url: string;
  published_at: string | null;
  duration_s: number | null;
  is_short: boolean | null;
  is_live: boolean | null;
  availability: string;
  sources: Source[];
  discovered_at: string | null;
  archive_state: ArchiveState;
  best_available_height: number | null;
  archived_height: number | null;
  job_id: number | null;
}

export interface MediaFileOut {
  id: number;
  video_id: string;
  title: string | null;
  channel_id: string | null;
  channel_title: string | null;
  thumbnail_url: string | null;
  url: string;
  rel_path: string;
  container: string;
  width: number;
  height: number;
  fps: number | null;
  vcodec: string | null;
  acodec: string | null;
  duration_s: number | null;
  size_bytes: number;
  is_hdr: boolean;
  source_format_id: string | null;
  max_height_requested: number | null;
  created_at: string; // when the file was archived
  published_at: string | null; // YouTube release date
  sources: Source[];
}

export interface LikesSyncRequest { full: boolean }
export interface LikesBackfillRequest { confirm: boolean }
export interface ManualEnqueueRequest { video_id: string; max_height: ResolutionCap }

export interface JobOut {
  id: number;
  video_id: string;
  title: string | null;
  channel_title: string | null;
  thumbnail_url: string | null;
  kind: 'archive' | 'upgrade';
  priority: number;
  status: JobStatus;
  stage: JobStage;
  max_height: number;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  progress_pct: number | null;
  bytes_downloaded: number | null;
  bytes_total: number | null;
  speed_bps: number | null;
  eta_s: number | null;
  error_code: string | null;
  error_message: string | null;
  selection: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type JobCounts = Record<JobStatus, number>;

export interface SettingsOut {
  archive_root: string;
  likes_mode: LikesMode;
  likes_poll_minutes: number;
  likes_full_reconcile_hours: number;
  likes_max_height: ResolutionCap;
  subscriptions_refresh_hours: number;
  channel_poll_minutes: number;
  default_max_height: ResolutionCap;
  audio_preference: 'aac' | 'opus';
  prefer_hdr: boolean;
  compatibility_profile: boolean;
  mkv_fallback: boolean;
  max_concurrent_downloads: number;
  bandwidth_limit_kbps: number | null;
  download_window: string | null;
  min_free_gb: number;
  large_channel_threshold: number;
  write_thumbnails: boolean;
  write_nfo: boolean;
  embed_metadata: boolean;
  keep_superseded_copies: boolean;
  quota_daily_budget: number;
  timezone: string;
}

export type SettingsUpdate = Partial<SettingsOut>;

export interface DirEntry { name: string; path: string; writable: boolean }
export interface BrowseResult { path: string; parent: string | null; mounts: string[]; entries: DirEntry[] }
export interface SetRootRequest { path: string; create: boolean }
export interface LogLines { lines: string[] }
export interface Health {
  status: 'ok' | 'degraded';
  version: string;
  db: boolean;
  worker: boolean;
  scheduler: boolean;
  archive_writable: boolean;
}

export interface SetupStatus {
  needs_setup: boolean;
  google_configured: boolean;
  emails_configured: boolean;
  google_env_managed: boolean;
  emails_env_managed: boolean;
  redirect_uri: string;
  base_url: string;
}
export interface SetupRequest {
  setup_code: string;
  google_client_id?: string | null;
  google_client_secret?: string | null;
  allowed_emails: string[];
  admin_emails: string[];
}
export interface GoogleSettingsOut {
  google_client_id: string | null;
  google_client_secret_set: boolean;
  allowed_emails: string[];
  admin_emails: string[];
  google_env_managed: boolean;
  emails_env_managed: boolean;
  redirect_uri: string;
}
export interface GoogleSettingsUpdate {
  google_client_id?: string | null;
  google_client_secret?: string | null;
  allowed_emails?: string[] | null;
  admin_emails?: string[] | null;
}

export interface DeleteVideoRequest { remove_like: boolean }
export interface DeleteVideoResult {
  video_id: string;
  deleted_files: string[];
  like_removed: boolean;
  excluded: boolean;
  warnings: string[];
}
export interface SidecarResult { written: number; skipped: number; failed: number }
export interface ReleaseDateResult { checked: number; from_youtube: number; from_files: number; archived_missing: number; error: string | null }
export interface SubscriptionDeleteResult { channel_id: string; title: string; archived_kept: number; jobs_cancelled: number }

export interface ImportFileOut {
  name: string;
  size_bytes: number;
  video_id: string | null;
  status: 'waiting' | 'failed' | 'imported';
  message: string | null;
}

export interface ImportFolderOut {
  path: string;
  files: ImportFileOut[];
}

export interface LibraryChannelOut {
  channel_id: string;
  title: string;
  count: number;
}
