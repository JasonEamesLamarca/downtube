from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import String, and_, cast, distinct, func, or_, select
from sqlalchemy.orm import Session

from downtube import scheduler
from downtube.api.rules import ConfirmationRequired, apply_rule_patch
from downtube.api.views import PageParams, user_video_ids
from downtube.api.youtube_write import call_youtube
from downtube.auth.audit import audit
from downtube.auth.deps import current_user, ensure_acknowledged, request_db
from downtube.auth.ratelimit import client_ip
from downtube.db import write_session
from downtube.main import ApiError
from downtube.models import (
    ArchiveMode,
    Channel,
    ChannelRule,
    DownloadJob,
    JobStatus,
    MediaFile,
    Subscription,
    User,
    Video,
)
from downtube.schemas import (
    BulkResult,
    BulkRuleRequest,
    ChannelEstimate,
    ChannelRuleOut,
    ChannelRulePatch,
    Page,
    SubscriptionOut,
    SyncRunOut,
)
from downtube.sync import runner
from downtube.sync.channels import estimate_channel

router = APIRouter(tags=["subscriptions"])


def _rule_out(rule: ChannelRule | None) -> ChannelRuleOut:
    if rule is None:
        return ChannelRuleOut()
    return ChannelRuleOut(
        mode=rule.mode.value,  # type: ignore[arg-type]
        paused=rule.paused,
        max_height=rule.max_height,  # type: ignore[arg-type]
        include_shorts=rule.include_shorts,
        include_live=rule.include_live,
        published_after=rule.published_after,
        max_duration_s=rule.max_duration_s,
        activated_at=rule.activated_at,
        backfill_completed_at=rule.backfill_completed_at,
    )


def _counts(db: Session, user_id: int, channel_ids: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {
        c: {"archived": 0, "queued": 0, "downloading": 0, "failed": 0} for c in channel_ids
    }
    if not channel_ids:
        return out
    mine = user_video_ids(user_id)
    for cid, n in db.execute(
        select(Video.channel_id, func.count(distinct(Video.id)))
        .join(MediaFile, and_(MediaFile.video_id == Video.id, MediaFile.is_current.is_(True)))
        .where(Video.channel_id.in_(channel_ids), Video.id.in_(mine))
        .group_by(Video.channel_id)
    ):
        if cid is not None:
            out[cid]["archived"] = n
    for cid, status, n in db.execute(
        select(Video.channel_id, DownloadJob.status, func.count(distinct(DownloadJob.video_id)))
        .join(Video, Video.id == DownloadJob.video_id)
        .where(Video.channel_id.in_(channel_ids), Video.id.in_(mine))
        .group_by(Video.channel_id, DownloadJob.status)
    ):
        key = {JobStatus.queued: "queued", JobStatus.paused: "queued", JobStatus.running: "downloading"}.get(
            status, "failed" if status == JobStatus.failed else None
        )
        if key and cid is not None:
            out[cid][key] += n
    return out


def _sub_out(sub: Subscription, ch: Channel, rule: ChannelRule | None, c: dict[str, int]) -> SubscriptionOut:
    return SubscriptionOut(
        channel_id=ch.id,
        title=ch.title,
        description=ch.description,
        thumbnail_url=ch.thumbnail_url,
        url=f"https://www.youtube.com/channel/{ch.id}",
        video_count=ch.video_count,
        subscribed=sub.active,
        rule=_rule_out(rule),
        sync_status=(rule.sync_status.value if rule else "idle"),  # type: ignore[arg-type]
        last_sync_at=rule.last_sync_at if rule else None,
        last_success_at=rule.last_success_at if rule else None,
        last_error=rule.last_error if rule else None,
        archived_count=c["archived"],
        queued_count=c["queued"],
        downloading_count=c["downloading"],
        failed_count=c["failed"],
    )


def _get_sub(db: Session, user: User, channel_id: str) -> tuple[Subscription, Channel, ChannelRule | None]:
    row = db.execute(
        select(Subscription, Channel)
        .join(Channel, Channel.id == Subscription.channel_id)
        .where(Subscription.user_id == user.id, Subscription.channel_id == channel_id)
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "subscription not found")
    rule = db.scalar(select(ChannelRule).where(ChannelRule.user_id == user.id, ChannelRule.channel_id == channel_id))
    return row[0], row[1], rule


@router.get("/subscriptions", response_model=Page[SubscriptionOut])
def list_subscriptions(
    q: str | None = None,
    sort: Literal["title", "archived", "last_sync", "video_count"] = "title",
    order: Literal["asc", "desc"] = "asc",
    mode: Literal["off", "backfill_once", "backfill_and_follow", "follow_new"] | None = None,
    status: Literal["idle", "running", "ok", "error", "quota_deferred"] | None = None,
    pp: PageParams = Depends(),
    user: User = Depends(current_user),
    db: Session = Depends(request_db),
) -> Page[SubscriptionOut]:
    mine = user_video_ids(user.id)
    arch = (
        select(Video.channel_id.label("cid"), func.count(distinct(Video.id)).label("n"))
        .join(MediaFile, and_(MediaFile.video_id == Video.id, MediaFile.is_current.is_(True)))
        .where(Video.id.in_(mine))
        .group_by(Video.channel_id)
        .subquery()
    )
    n_arch = func.coalesce(arch.c.n, 0)
    base = (
        select(Subscription, Channel, ChannelRule)
        .join(Channel, Channel.id == Subscription.channel_id)
        .outerjoin(ChannelRule, and_(ChannelRule.channel_id == Channel.id, ChannelRule.user_id == user.id))
        .outerjoin(arch, arch.c.cid == Channel.id)
        .where(Subscription.user_id == user.id)
    )
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.where(Channel.title.ilike(f"%{esc}%", escape="\\"))
    if mode == "off":
        base = base.where(or_(ChannelRule.id.is_(None), cast(ChannelRule.mode, String) == "off"))
    elif mode:
        base = base.where(cast(ChannelRule.mode, String) == mode)
    if status == "idle":
        base = base.where(or_(ChannelRule.id.is_(None), cast(ChannelRule.sync_status, String) == "idle"))
    elif status:
        base = base.where(cast(ChannelRule.sync_status, String) == status)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    key = {
        "title": func.lower(Channel.title),
        "archived": n_arch,
        "last_sync": ChannelRule.last_sync_at,
        "video_count": Channel.video_count,
    }[sort]
    base = base.order_by(key.desc() if order == "desc" else key.asc(), Channel.id)
    rows = db.execute(base.offset(pp.offset).limit(pp.page_size)).all()
    counts = _counts(db, user.id, [r[1].id for r in rows])
    return Page[SubscriptionOut](
        items=[_sub_out(sub, ch, rule, counts[ch.id]) for sub, ch, rule in rows],
        total=total,
        page=pp.page,
        page_size=pp.page_size,
    )


@router.post("/subscriptions/sync", response_model=SyncRunOut, status_code=202)
def sync_subscriptions(user: User = Depends(current_user)) -> SyncRunOut:
    return SyncRunOut.model_validate(runner.start("subscriptions", user.id))


@router.get("/subscriptions/{channel_id}/estimate", response_model=ChannelEstimate)
def estimate(channel_id: str, user: User = Depends(current_user), db: Session = Depends(request_db)) -> ChannelEstimate:
    _get_sub(db, user, channel_id)
    return estimate_channel(user.id, channel_id)


def _confirmation_error(e: ConfirmationRequired) -> ApiError:
    return ApiError(
        409,
        "confirmation_required",
        "this channel is large; confirm to start a full backfill",
        {"estimate": e.estimate.model_dump(mode="json")},
    )


@router.patch("/subscriptions/{channel_id}/rule", response_model=SubscriptionOut)
def patch_rule(channel_id: str, patch: ChannelRulePatch, user: User = Depends(current_user)) -> SubscriptionOut:
    if patch.mode is not None and patch.mode != "off":
        ensure_acknowledged(user)
    with write_session() as w:
        sub, ch, _ = _get_sub(w, user, channel_id)
        try:
            rule = apply_rule_patch(w, user.id, channel_id, patch)
        except ConfirmationRequired as e:
            raise _confirmation_error(e) from e
        out = _sub_out(sub, ch, rule, _counts(w, user.id, [channel_id])[channel_id])
        active = rule.mode != ArchiveMode.off and not rule.paused
    if active:
        scheduler.kick_channel(user.id, channel_id)  # after commit, so the sync thread sees the rule
    return out


@router.post("/subscriptions/bulk", response_model=BulkResult)
def bulk(body: BulkRuleRequest, user: User = Depends(current_user)) -> BulkResult:
    if body.patch.mode is not None and body.patch.mode != "off":
        ensure_acknowledged(user)
    result = BulkResult(updated=[], needs_confirmation=[], errors={})
    to_kick: list[str] = []
    with write_session() as w:
        for cid in dict.fromkeys(body.channel_ids):
            if (
                w.scalar(select(Subscription.id).where(Subscription.user_id == user.id, Subscription.channel_id == cid))
                is None
            ):
                result.errors[cid] = "not subscribed"
                continue
            try:
                rule = apply_rule_patch(w, user.id, cid, body.patch)
            except ConfirmationRequired as e:  # raised before any mutation of that rule
                result.needs_confirmation.append(e.estimate)
                continue
            result.updated.append(cid)
            if rule.mode != ArchiveMode.off and not rule.paused:
                to_kick.append(cid)
    for cid in to_kick:
        scheduler.kick_channel(user.id, cid)
    return result


@router.post("/subscriptions/{channel_id}/sync", response_model=SyncRunOut, status_code=202)
def sync_one(channel_id: str, user: User = Depends(current_user), db: Session = Depends(request_db)) -> SyncRunOut:
    _get_sub(db, user, channel_id)
    ensure_acknowledged(user)
    rule = db.scalar(select(ChannelRule).where(ChannelRule.user_id == user.id, ChannelRule.channel_id == channel_id))
    if rule is None or rule.mode == ArchiveMode.off:
        raise ApiError(409, "rule_inactive", "this channel has no active archive rule")
    return SyncRunOut.model_validate(runner.start("channel", user.id, channel_id))


@router.post("/subscriptions/{channel_id}/unsubscribe", response_model=SubscriptionOut)
def unsubscribe(
    channel_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(request_db)
) -> SubscriptionOut:
    """Unsubscribe on YouTube, turn the channel's archive rule off and drop its queued downloads.

    Videos already archived stay in the library.
    """
    sub, _, _ = _get_sub(db, user, channel_id)
    if not sub.active:
        raise ApiError(409, "not_subscribed", "you are no longer subscribed to this channel")

    def _do(yt) -> bool:  # type: ignore[no-untyped-def]
        sid = yt.subscription_id(channel_id)
        if sid:
            yt.delete_subscription(sid)
        return sid is not None

    removed_upstream = call_youtube(user.id, _do)
    with write_session() as w:
        sub, ch, rule = _get_sub(w, user, channel_id)
        sub.active = False
        if rule is not None and rule.mode != ArchiveMode.off:
            rule = apply_rule_patch(w, user.id, channel_id, ChannelRulePatch(mode="off"))
        audit(
            w,
            "subscription.unsubscribe",
            user.id,
            {"channel_id": channel_id, "removed_on_youtube": removed_upstream},
            client_ip(request),
        )
        w.flush()
        return _sub_out(sub, ch, rule, _counts(w, user.id, [channel_id])[channel_id])
