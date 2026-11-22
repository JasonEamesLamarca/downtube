"""Account lifecycle: disconnect (optional token revocation and local data deletion)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, exists, select, update

from downtube.auth.audit import audit
from downtube.config import get_config
from downtube.db import write_session
from downtube.downloads import jobs
from downtube.models import (
    ACTIVE_JOB_STATUSES,
    AccountStatus,
    Channel,
    ChannelRule,
    DownloadJob,
    GoogleAccount,
    MediaFile,
    Subscription,
    SyncCheckpoint,
    SyncRun,
    Video,
    VideoExclusion,
    VideoSource,
    WebSession,
)
from downtube.security.crypto import CryptoError, decrypt

log = logging.getLogger("downtube.auth")


def revoke_at_google(token: str) -> bool:
    try:
        with httpx.Client(timeout=15) as c:
            return c.post(get_config().google_revoke_url, data={"token": token}).status_code == 200
    except httpx.HTTPError:
        return False


def disconnect(user_id: int, *, revoke: bool, delete_local_data: bool, ip: str | None = None) -> dict:
    """Disconnect the user's Google account.

    Tokens are always wiped locally. ``delete_local_data`` removes the user's subscriptions, rules,
    video sources, sync data, sessions and account row. Archived files and MediaFile rows are never
    deleted, so videos other users still reference are unaffected.
    """
    token: str | None = None
    with write_session() as s:
        acc = s.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
        if acc is not None:
            try:
                enc = acc.refresh_token_enc or acc.access_token_enc
                token = decrypt(enc) if enc else None
            except CryptoError:
                token = None
    revoked = revoke_at_google(token) if (revoke and token) else False

    summary: dict = {"revoked": revoked, "delete_local_data": delete_local_data}
    with write_session() as s:
        acc = s.scalar(select(GoogleAccount).where(GoogleAccount.user_id == user_id))
        if acc is not None:
            acc.access_token_enc = acc.refresh_token_enc = None
            acc.token_expires_at = None
            acc.status = AccountStatus.disconnected
            acc.status_detail = "disconnected by user"
        if delete_local_data:
            summary.update(_delete_user_data(s, user_id))
        audit(s, "account.disconnect", user_id, summary, ip)
    return summary


def _delete_user_data(s, user_id: int) -> dict:  # type: ignore[no-untyped-def]
    touched = list(s.scalars(select(VideoSource.video_id).where(VideoSource.user_id == user_id).distinct()))
    counts = {
        "sources": s.execute(delete(VideoSource).where(VideoSource.user_id == user_id)).rowcount,
        "rules": s.execute(delete(ChannelRule).where(ChannelRule.user_id == user_id)).rowcount,
        "subscriptions": s.execute(delete(Subscription).where(Subscription.user_id == user_id)).rowcount,
    }
    s.execute(delete(VideoExclusion).where(VideoExclusion.user_id == user_id))
    s.execute(delete(SyncRun).where(SyncRun.user_id == user_id))
    s.execute(delete(SyncCheckpoint).where(SyncCheckpoint.user_id == user_id))
    s.execute(delete(WebSession).where(WebSession.user_id == user_id))
    s.execute(delete(GoogleAccount).where(GoogleAccount.user_id == user_id))
    s.flush()
    # Jobs nobody else wants any more are cancelled; user attribution is dropped.
    for job in s.scalars(
        select(DownloadJob).where(DownloadJob.status.in_(ACTIVE_JOB_STATUSES), DownloadJob.video_id.in_(touched))
    ):
        if jobs.compute_effective_cap(s, job.video_id) is None:
            jobs.cancel_job(s, job)
    s.execute(update(DownloadJob).where(DownloadJob.user_id == user_id).values(user_id=None))
    # Drop API metadata nobody references (never anything tied to an archived file or a job).
    for vid in touched:
        if not (
            s.scalar(select(exists().where(VideoSource.video_id == vid)))
            or s.scalar(select(exists().where(MediaFile.video_id == vid)))
            or s.scalar(select(exists().where(DownloadJob.video_id == vid)))
        ):
            s.execute(delete(Video).where(Video.id == vid))
    s.flush()
    for cid in list(s.scalars(select(Channel.id))):
        if not (
            s.scalar(select(exists().where(Video.channel_id == cid)))
            or s.scalar(select(exists().where(Subscription.channel_id == cid)))
            or s.scalar(select(exists().where(ChannelRule.channel_id == cid)))
        ):
            s.execute(delete(Channel).where(Channel.id == cid))
    counts["deleted_at"] = datetime.now(UTC).isoformat()
    return counts
