import type { JobOut, SettingsOut, SubscriptionOut } from '../api/types';

export const sub = (over: Partial<SubscriptionOut> = {}): SubscriptionOut => ({
  channel_id: 'UC1', title: 'Channel One', description: 'About one', thumbnail_url: null,
  url: 'https://www.youtube.com/channel/UC1', video_count: 120, subscribed: true,
  rule: { mode: 'off', paused: false, max_height: null, include_shorts: true, include_live: true, published_after: null, max_duration_s: null, activated_at: null, backfill_completed_at: null },
  sync_status: 'idle', last_sync_at: null, last_success_at: null, last_error: null,
  archived_count: 3, queued_count: 1, downloading_count: 0, failed_count: 0, ...over,
});

export const job = (over: Partial<JobOut> = {}): JobOut => ({
  id: 1, video_id: 'dQw4w9WgXcQ', title: 'Job Video', channel_title: 'Chan', thumbnail_url: null, kind: 'archive', priority: 10,
  status: 'running', stage: 'downloading', max_height: 2160, attempts: 1, max_attempts: 5, next_attempt_at: null,
  progress_pct: 42, bytes_downloaded: 1000, bytes_total: 2000, speed_bps: 1024, eta_s: 30, error_code: null, error_message: null,
  selection: null, created_at: '2026-01-01T00:00:00Z', started_at: null, finished_at: null, ...over,
});

export const settings: SettingsOut = {
  archive_root: '/archive', likes_mode: 'future_only', likes_poll_minutes: 15, likes_full_reconcile_hours: 24, likes_max_height: null,
  subscriptions_refresh_hours: 24, channel_poll_minutes: 60, default_max_height: null, audio_preference: 'aac', prefer_hdr: false,
  compatibility_profile: false, mkv_fallback: true, max_concurrent_downloads: 2, bandwidth_limit_kbps: null, download_window: null,
  min_free_gb: 20, large_channel_threshold: 200, write_thumbnails: true, write_nfo: false, keep_superseded_copies: true,
  quota_daily_budget: 8000, timezone: 'UTC',
};
