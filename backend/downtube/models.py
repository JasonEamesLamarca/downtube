"""SQLAlchemy ORM models — the canonical persistence contract for DownTube.

Identity rules
--------------
* YouTube video IDs and channel IDs are immutable and are used as primary keys
  (``Video.id``, ``Channel.id``). Display names are attributes only.
* One canonical ``Video`` row per YouTube video, regardless of how many users or
  discovery sources reference it. Discovery is tracked in ``VideoSource``.
* One current ``MediaFile`` per video (``is_current=True``). Upgrades insert a new
  row and flip the flag only after the replacement validated successfully.

All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True), dict: JSON, list: JSON}


# --------------------------------------------------------------------------- enums


class AccountStatus(enum.StrEnum):
    active = "active"
    reauth_required = "reauth_required"  # refresh failed (expired / revoked)
    disconnected = "disconnected"


class DiscoverySource(enum.StrEnum):
    like = "like"
    subscription = "subscription"
    manual = "manual"


class ArchiveMode(enum.StrEnum):
    off = "off"  # not archiving (default); existing files untouched
    backfill_once = "backfill_once"  # all existing uploads, once
    backfill_and_follow = "backfill_and_follow"  # all existing + new uploads
    follow_new = "follow_new"  # only uploads published after activation


class LikesMode(enum.StrEnum):
    off = "off"
    future_only = "future_only"  # baseline existing likes, archive only new ones
    all = "all"  # archive historical likes too


class SyncStatus(enum.StrEnum):
    idle = "idle"
    running = "running"
    ok = "ok"
    error = "error"
    quota_deferred = "quota_deferred"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"  # terminal after max attempts or non-retryable error
    cancelled = "cancelled"
    unsupported = "unsupported"  # no acceptable stream/container combination
    skipped = "skipped"  # e.g. already archived at >= quality, video unavailable


ACTIVE_JOB_STATUSES = (JobStatus.queued, JobStatus.running, JobStatus.paused)


class JobStage(enum.StrEnum):
    pending = "pending"
    probing = "probing"  # yt-dlp -J, format selection
    downloading = "downloading"
    merging = "merging"  # ffmpeg remux (stream copy)
    validating = "validating"  # ffprobe
    finalizing = "finalizing"  # atomic move into archive
    done = "done"


class JobKind(enum.StrEnum):
    archive = "archive"
    upgrade = "upgrade"  # replace an existing lower-resolution archive


class Availability(enum.StrEnum):
    public = "public"
    unlisted = "unlisted"
    private = "private"
    deleted = "deleted"
    unknown = "unknown"


# --------------------------------------------------------------------------- users & auth


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)  # OIDC subject
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(String(1000))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_terms_at: Mapped[datetime | None]  # rights/responsibility acknowledgement
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None]

    accounts: Mapped[list[GoogleAccount]] = relationship(back_populates="user", cascade="all, delete-orphan")


class GoogleAccount(Base):
    """A connected Google/YouTube account. Tokens are Fernet-encrypted at rest."""

    __tablename__ = "google_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64))
    scopes: Mapped[str] = mapped_column(Text, default="")
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None]
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus, native_enum=False), default=AccountStatus.active)
    status_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")


class WebSession(Base):
    """Server-side session. Cookie holds an opaque random id; only its SHA-256 is stored."""

    __tablename__ = "web_sessions"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(400))


class OAuthState(Base):
    """Pending OAuth authorization request (state + PKCE verifier + nonce). Single use, short TTL."""

    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_verifier_enc: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


# --------------------------------------------------------------------------- YouTube entities


class Channel(Base):
    """YouTube channel metadata (API data — refreshed periodically, see PRIVACY.md)."""

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UC...
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    custom_url: Mapped[str | None] = mapped_column(String(200))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))
    video_count: Mapped[int | None] = mapped_column(Integer)
    # Directory name under the archive root. Assigned on first archive and then NEVER
    # changed automatically, so a channel rename cannot split or duplicate the archive.
    folder_name: Mapped[str | None] = mapped_column(String(255), unique=True)
    metadata_fetched_at: Mapped[datetime | None]


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # False once unsubscribed upstream
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)

    channel: Mapped[Channel] = relationship()


class ChannelRule(Base):
    """Per-user archive rule for a channel (Method B)."""

    __tablename__ = "channel_rules"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    mode: Mapped[ArchiveMode] = mapped_column(Enum(ArchiveMode, native_enum=False), default=ArchiveMode.off)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    max_height: Mapped[int | None] = mapped_column(Integer)  # None = best available (capped at 2160)
    include_shorts: Mapped[bool] = mapped_column(Boolean, default=True)
    include_live: Mapped[bool] = mapped_column(Boolean, default=True)
    published_after: Mapped[datetime | None]  # exclude older uploads
    max_duration_s: Mapped[int | None] = mapped_column(Integer)
    activated_at: Mapped[datetime | None]
    backfill_completed_at: Mapped[datetime | None]
    backfill_page_token: Mapped[str | None] = mapped_column(String(200))  # resumable backfill checkpoint
    sync_status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, native_enum=False), default=SyncStatus.idle)
    last_sync_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    channel: Mapped[Channel] = relationship()


class Video(Base):
    """Canonical video record (one per YouTube video ID)."""

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 11-char YouTube id
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"), index=True)
    channel_title: Mapped[str | None] = mapped_column(String(500))  # title as seen at discovery
    title: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None]
    duration_s: Mapped[int | None] = mapped_column(Integer)
    is_short: Mapped[bool | None] = mapped_column(Boolean)
    is_live: Mapped[bool | None] = mapped_column(Boolean)  # (completed) livestream recording
    live_status: Mapped[str | None] = mapped_column(String(20))  # none | upcoming | live | completed
    availability: Mapped[Availability] = mapped_column(
        Enum(Availability, native_enum=False), default=Availability.unknown
    )
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000))
    metadata_fetched_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sources: Mapped[list[VideoSource]] = relationship(back_populates="video", cascade="all, delete-orphan")
    media_files: Mapped[list[MediaFile]] = relationship(back_populates="video")

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


class VideoSource(Base):
    """Why a user wants a video: liked, from a channel rule, or manually added."""

    __tablename__ = "video_sources"
    __table_args__ = (UniqueConstraint("user_id", "video_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    source: Mapped[DiscoverySource] = mapped_column(Enum(DiscoverySource, native_enum=False))
    channel_rule_id: Mapped[int | None] = mapped_column(ForeignKey("channel_rules.id", ondelete="SET NULL"))
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    # False when the like was removed upstream. The archive is NEVER deleted because of it.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # False for the baseline of a "future_only" likes scan: known, but not to be archived.
    archive_eligible: Mapped[bool] = mapped_column(Boolean, default=True)

    video: Mapped[Video] = relationship(back_populates="sources")


class MediaFile(Base):
    """A validated archived file. Paths are relative to the archive root in ``root_path``."""

    __tablename__ = "media_files"
    __table_args__ = (Index("uq_media_current", "video_id", unique=True, sqlite_where=text("is_current = 1")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    root_path: Mapped[str] = mapped_column(String(1000))  # container path of the archive root at write time
    rel_path: Mapped[str] = mapped_column(String(1000))  # "<folder>/<file>.mp4"
    container: Mapped[str] = mapped_column(String(16))  # mp4 | mkv
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    vcodec: Mapped[str | None] = mapped_column(String(64))
    acodec: Mapped[str | None] = mapped_column(String(64))
    duration_s: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    is_hdr: Mapped[bool] = mapped_column(Boolean, default=False)
    source_format_id: Mapped[str | None] = mapped_column(String(64))  # e.g. "401+140"
    source_format_note: Mapped[str | None] = mapped_column(Text)
    max_height_requested: Mapped[int | None] = mapped_column(Integer)
    best_available_height: Mapped[int | None] = mapped_column(Integer)  # best original <= 2160 at probe time
    available_formats: Mapped[list | None] = mapped_column(JSON)  # compact summary of original formats
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    superseded_at: Mapped[datetime | None]
    # Upgrades never delete the previous copy automatically; it is moved/kept per settings.
    superseded_action: Mapped[str | None] = mapped_column(String(32))

    video: Mapped[Video] = relationship(back_populates="media_files")


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    __table_args__ = (
        # At most one active job per video.
        Index(
            "uq_job_active_video",
            "video_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running','paused')"),
        ),
        Index("ix_job_pick", "status", "priority", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[JobKind] = mapped_column(Enum(JobKind, native_enum=False), default=JobKind.archive)
    # Lower number = higher priority. Likes=10, manual=20, subscription=50, backfill=80.
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, native_enum=False), default=JobStatus.queued)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage, native_enum=False), default=JobStage.pending)
    max_height: Mapped[int] = mapped_column(Integer, default=2160)  # effective cap (<= 2160)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_attempt_at: Mapped[datetime | None]
    progress_pct: Mapped[float | None] = mapped_column(Float)
    bytes_downloaded: Mapped[int | None] = mapped_column(BigInteger)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    speed_bps: Mapped[float | None] = mapped_column(Float)
    eta_s: Mapped[int | None] = mapped_column(Integer)
    selection: Mapped[dict | None] = mapped_column(JSON)  # chosen formats + reasoning
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    media_file_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship()


# --------------------------------------------------------------------------- sync bookkeeping


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # likes | likes_full | subscriptions | channel
    target: Mapped[str | None] = mapped_column(String(64))  # channel id for kind=channel
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule | manual | startup
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, native_enum=False), default=SyncStatus.running)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_queued: Mapped[int] = mapped_column(Integer, default=0)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("user_id", "kind", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class QuotaUsage(Base):
    """YouTube Data API units consumed per Pacific-time day (quota resets at PT midnight)."""

    __tablename__ = "quota_usage"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD (America/Los_Angeles)
    units: Mapped[int] = mapped_column(Integer, default=0)
    exhausted_at: Mapped[datetime | None]


class Setting(Base):
    """Application settings as JSON values. See ``settings_service.DEFAULTS``."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))  # login, logout, settings.update, account.disconnect, ...
    detail: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
