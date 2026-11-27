"""Pydantic models for the public REST API — the frontend/backend contract.

The frontend mirrors these in ``frontend/src/api/types.ts``. Change both together.
All datetimes are ISO-8601 UTC strings on the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

# Allowed resolution caps. None = "best available" (still hard-capped at 2160).
ResolutionCap = Literal[2160, 1440, 1080, 720, 480, 360] | None
RESOLUTION_CAPS: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360)

ArchiveModeT = Literal["off", "backfill_once", "backfill_and_follow", "follow_new"]
LikesModeT = Literal["off", "future_only", "all"]
JobStatusT = Literal["queued", "running", "paused", "completed", "failed", "cancelled", "unsupported", "skipped"]
JobStageT = Literal["pending", "probing", "downloading", "merging", "validating", "finalizing", "done"]
SyncStatusT = Literal["idle", "running", "ok", "error", "quota_deferred"]
SourceT = Literal["like", "subscription", "manual"]
ArchiveStateT = Literal["not_archived", "queued", "downloading", "archived", "failed", "unsupported", "excluded"]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](ApiModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(ApiModel):
    """Every non-2xx response: ``{"error": {"code": "...", "message": "..."}}``."""

    code: str
    message: str


# ----------------------------------------------------------------- auth / account


class Me(ApiModel):
    id: int
    email: str
    display_name: str | None
    picture_url: str | None
    is_admin: bool
    acknowledged_terms: bool
    account_status: Literal["active", "reauth_required", "disconnected"]
    youtube_channel_id: str | None
    can_manage_youtube: bool = False  # optional permission for unsubscribe / remove like was granted
    csrf_token: str  # send back as X-CSRF-Token on every mutating request


class DisconnectRequest(ApiModel):
    revoke_google_token: bool = True
    delete_local_data: bool = False  # deletes DB rows for this user; NEVER deletes archived media files


# ----------------------------------------------------------------- dashboard


class StorageInfo(ApiModel):
    root_path: str  # container path, e.g. /archive
    host_path_hint: str | None  # from DOWNTUBE_HOST_ARCHIVE_PATH, display only
    total_bytes: int
    free_bytes: int
    archive_bytes: int  # sum of current media files
    min_free_bytes: int
    below_threshold: bool
    writable: bool


class QuotaInfo(ApiModel):
    day: str
    used: int
    budget: int
    exhausted: bool
    resets_at: datetime


class SyncRunOut(ApiModel):
    id: int
    kind: str
    target: str | None
    trigger: str
    status: SyncStatusT
    started_at: datetime
    finished_at: datetime | None
    items_seen: int
    items_new: int
    jobs_queued: int
    quota_used: int
    error: str | None


class BotCheckRetryOut(ApiModel):
    resumed: bool  # False: downloads were not paused


class Dashboard(ApiModel):
    account_email: str | None
    account_status: str
    archived_videos: int
    archive_bytes: int
    monitored_channels: int  # rules with mode != off and not paused
    liked_videos_discovered: int
    downloads_active: int
    downloads_pending: int
    downloads_failed: int
    current_speed_bps: float
    downloads_paused_until: datetime | None = None  # YouTube bot check: all downloads wait until then
    bot_checks_in_a_row: int = 0
    storage: StorageInfo
    quota: QuotaInfo
    likes_mode: LikesModeT
    last_likes_sync: datetime | None
    recent_syncs: list[SyncRunOut]


# ----------------------------------------------------------------- subscriptions / rules


class ChannelRuleOut(ApiModel):
    mode: ArchiveModeT = "off"
    paused: bool = False
    max_height: ResolutionCap = None
    include_shorts: bool = True
    include_live: bool = True
    published_after: datetime | None = None
    max_duration_s: int | None = None
    activated_at: datetime | None = None
    backfill_completed_at: datetime | None = None


class ChannelRulePatch(ApiModel):
    """Partial update; omitted fields unchanged. Activating a backfill mode on a channel whose
    estimate exceeds the large-channel threshold requires ``confirm_large=True``."""

    mode: ArchiveModeT | None = None
    paused: bool | None = None
    max_height: ResolutionCap = None
    clear_max_height: bool = False  # set max_height back to "best available"
    include_shorts: bool | None = None
    include_live: bool | None = None
    published_after: datetime | None = None
    max_duration_s: int | None = Field(default=None, ge=1)
    confirm_large: bool = False


class BulkRuleRequest(ApiModel):
    channel_ids: list[str] = Field(min_length=1, max_length=1000)
    patch: ChannelRulePatch


class SubscriptionOut(ApiModel):
    channel_id: str
    title: str
    description: str | None
    thumbnail_url: str | None
    url: str  # https://www.youtube.com/channel/<id>
    video_count: int | None
    subscribed: bool
    rule: ChannelRuleOut
    sync_status: SyncStatusT
    last_sync_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    archived_count: int
    queued_count: int
    downloading_count: int
    failed_count: int


class SubscriptionDeleteResult(ApiModel):
    channel_id: str
    title: str
    archived_kept: int  # archived videos of the channel that stay in the library
    jobs_cancelled: int  # queued downloads dropped because the archive rule was still on


class ChannelEstimate(ApiModel):
    channel_id: str
    video_count: int | None
    estimated_bytes: int | None  # None when not reliably estimable
    estimate_basis: str  # human-readable explanation of how the estimate was made
    is_large: bool
    requires_confirmation: bool


class BulkResult(ApiModel):
    updated: list[str]
    needs_confirmation: list[ChannelEstimate]
    errors: dict[str, str]


# ----------------------------------------------------------------- videos / likes / library


class VideoOut(ApiModel):
    video_id: str
    title: str | None
    channel_id: str | None
    channel_title: str | None
    thumbnail_url: str | None
    url: str
    published_at: datetime | None
    duration_s: int | None
    is_short: bool | None
    is_live: bool | None
    availability: str
    sources: list[SourceT]
    discovered_at: datetime | None
    archive_state: ArchiveStateT
    best_available_height: int | None  # best original stream <= 2160 seen at probe time
    archived_height: int | None
    job_id: int | None


class MediaFileOut(ApiModel):
    id: int
    video_id: str
    title: str | None
    channel_id: str | None
    channel_title: str | None
    thumbnail_url: str | None  # API thumbnail, or /api/library/{video_id}/thumbnail if local
    url: str
    rel_path: str
    container: str
    width: int
    height: int
    fps: float | None
    vcodec: str | None
    acodec: str | None
    duration_s: float | None
    size_bytes: int
    is_hdr: bool
    source_format_id: str | None
    max_height_requested: int | None
    created_at: datetime  # when the file was archived
    published_at: datetime | None  # YouTube release date (None until known)
    sources: list[SourceT]


class DeleteVideoRequest(ApiModel):
    # Also remove the like on YouTube when the video is liked (needs the manage permission).
    remove_like: bool = True


class DeleteVideoResult(ApiModel):
    video_id: str
    deleted_files: list[str]  # paths relative to the archive root
    like_removed: bool
    excluded: bool  # never downloaded again for this user (until requested manually)
    warnings: list[str]


class SidecarResult(ApiModel):
    written: int
    skipped: int
    failed: int


class ReleaseDateResult(ApiModel):
    checked: int  # videos without a release date that were looked up on YouTube
    from_youtube: int
    from_files: int  # taken from the date tag embedded in the archived file
    archived_missing: int  # archived videos whose release date is still unknown
    error: str | None = None  # why the YouTube lookup stopped early (quota, no account, ...)


class LikesSyncRequest(ApiModel):
    full: bool = False  # full reconciliation instead of incremental poll


class LikesBackfillRequest(ApiModel):
    """Queue all known (baseline) likes that were not yet archived."""

    confirm: bool = False


class ManualEnqueueRequest(ApiModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    max_height: ResolutionCap = None


# ----------------------------------------------------------------- downloads


class JobOut(ApiModel):
    id: int
    video_id: str
    title: str | None
    channel_title: str | None
    thumbnail_url: str | None
    kind: Literal["archive", "upgrade"]
    priority: int
    status: JobStatusT
    stage: JobStageT
    max_height: int
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    progress_pct: float | None
    bytes_downloaded: int | None
    bytes_total: int | None
    speed_bps: float | None
    eta_s: int | None
    error_code: str | None
    error_message: str | None
    selection: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobCounts(ApiModel):
    queued: int
    running: int
    paused: int
    completed: int
    failed: int
    cancelled: int
    unsupported: int
    skipped: int


# ----------------------------------------------------------------- settings / storage


class SettingsOut(ApiModel):
    archive_root: str
    likes_mode: LikesModeT
    likes_poll_minutes: int = Field(ge=5, le=1440)
    likes_full_reconcile_hours: int = Field(ge=1, le=720)
    likes_max_height: ResolutionCap  # quality for liked videos (overrides channel caps)
    subscriptions_refresh_hours: int = Field(ge=1, le=720)
    channel_poll_minutes: int = Field(ge=15, le=10080)
    default_max_height: ResolutionCap
    audio_preference: Literal["aac", "opus"]
    prefer_hdr: bool
    mkv_fallback: bool
    max_concurrent_downloads: int = Field(ge=1, le=8)
    bandwidth_limit_kbps: int | None = Field(default=None, ge=64)  # None = unlimited
    download_window: str | None  # "HH:MM-HH:MM" local time, None = always
    min_free_gb: int = Field(ge=1)
    large_channel_threshold: int = Field(ge=1)  # videos; above this, confirmation required
    write_thumbnails: bool
    write_nfo: bool
    embed_metadata: bool  # title/description/date tags inside the MP4 (stream copy, no re-encode)
    keep_superseded_copies: bool  # on upgrade: keep old file renamed *.superseded instead of deleting
    quota_daily_budget: int = Field(ge=100, le=1_000_000)
    timezone: str


class SettingsUpdate(ApiModel):
    """Any subset of SettingsOut fields."""

    model_config = ConfigDict(extra="forbid")

    archive_root: str | None = None
    likes_mode: LikesModeT | None = None
    likes_poll_minutes: int | None = Field(default=None, ge=5, le=1440)
    likes_full_reconcile_hours: int | None = Field(default=None, ge=1, le=720)
    likes_max_height: ResolutionCap = None
    subscriptions_refresh_hours: int | None = Field(default=None, ge=1, le=720)
    channel_poll_minutes: int | None = Field(default=None, ge=15, le=10080)
    default_max_height: ResolutionCap = None
    audio_preference: Literal["aac", "opus"] | None = None
    prefer_hdr: bool | None = None
    mkv_fallback: bool | None = None
    max_concurrent_downloads: int | None = Field(default=None, ge=1, le=8)
    bandwidth_limit_kbps: int | None = Field(default=None, ge=0)  # 0 = unlimited
    download_window: str | None = None  # "" = always
    min_free_gb: int | None = Field(default=None, ge=1)
    large_channel_threshold: int | None = Field(default=None, ge=1)
    write_thumbnails: bool | None = None
    write_nfo: bool | None = None
    embed_metadata: bool | None = None
    keep_superseded_copies: bool | None = None
    quota_daily_budget: int | None = Field(default=None, ge=100, le=1_000_000)
    timezone: str | None = None


class DirEntry(ApiModel):
    name: str
    path: str  # container path
    writable: bool


class BrowseResult(ApiModel):
    path: str
    parent: str | None  # None at an allowed mount root
    mounts: list[str]
    entries: list[DirEntry]


class SetRootRequest(ApiModel):
    path: str
    create: bool = False


class LogLines(ApiModel):
    lines: list[str]


class Health(ApiModel):
    status: Literal["ok", "degraded"]
    version: str
    db: bool
    worker: bool
    scheduler: bool
    archive_writable: bool


# ----------------------------------------------------------------- first-run setup / Google sign-in config (v0.2)


class SetupStatus(ApiModel):
    """Public (no auth). ``needs_setup`` → the SPA shows /setup instead of /login."""

    needs_setup: bool
    google_configured: bool
    emails_configured: bool
    google_env_managed: bool  # GOOGLE_CLIENT_ID/SECRET come from the environment (read-only in the UI)
    emails_env_managed: bool  # DOWNTUBE_ALLOWED_EMAILS comes from the environment
    redirect_uri: str  # register exactly this in Google Cloud Console
    base_url: str


class SetupRequest(ApiModel):
    """POST /api/setup (no auth). ``setup_code`` is printed in the container log on startup."""

    setup_code: str = Field(min_length=6, max_length=64)
    google_client_id: str | None = Field(default=None, max_length=255)
    google_client_secret: str | None = Field(default=None, max_length=255)
    allowed_emails: list[str] = Field(default_factory=list, max_length=50)
    admin_emails: list[str] = Field(default_factory=list, max_length=50)


class GoogleSettingsOut(ApiModel):
    """GET /api/admin/google (admin). The secret itself is never returned."""

    google_client_id: str | None
    google_client_secret_set: bool
    allowed_emails: list[str]
    admin_emails: list[str]
    google_env_managed: bool
    emails_env_managed: bool
    redirect_uri: str


class GoogleSettingsUpdate(ApiModel):
    """PUT /api/admin/google (admin). Omitted/None fields are unchanged. You cannot remove your own email."""

    model_config = ConfigDict(extra="forbid")

    google_client_id: str | None = Field(default=None, max_length=255)
    google_client_secret: str | None = Field(default=None, max_length=255)
    allowed_emails: list[str] | None = Field(default=None, max_length=50)
    admin_emails: list[str] | None = Field(default=None, max_length=50)


class ImportFileOut(ApiModel):
    name: str
    size_bytes: int
    video_id: str | None
    status: Literal["waiting", "failed", "imported"]
    message: str | None


class ImportFolderOut(ApiModel):
    path: str
    files: list[ImportFileOut]


class LibraryChannelOut(ApiModel):
    channel_id: str
    title: str
    count: int
