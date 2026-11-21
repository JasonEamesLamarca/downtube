"""Channel-rule mutation logic shared by the single and bulk PATCH endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from downtube.downloads import jobs
from downtube.models import (
    ArchiveMode,
    ChannelRule,
    DiscoverySource,
    DownloadJob,
    JobStatus,
    SyncStatus,
    VideoSource,
)
from downtube.schemas import ChannelEstimate, ChannelRulePatch
from downtube.sync.channels import BACKFILL_MODES, estimate_channel
from downtube.sync.filters import _aware


class ConfirmationRequired(Exception):
    def __init__(self, estimate: ChannelEstimate) -> None:
        super().__init__("confirmation required")
        self.estimate = estimate


def _set_fields(patch: ChannelRulePatch) -> set[str]:
    return patch.model_fields_set


def deactivate_rule_sources(s: Session, rule: ChannelRule) -> None:
    """Rule turned off/paused: stop wanting its videos and drop queued (not running) jobs nobody else wants."""
    vids = []
    for src in s.scalars(
        select(VideoSource).where(
            VideoSource.channel_rule_id == rule.id, VideoSource.source == DiscoverySource.subscription
        )
    ):
        if src.active:
            src.active = False
            vids.append(src.video_id)
    s.flush()
    if not vids:
        return
    for job in s.scalars(
        select(DownloadJob).where(
            DownloadJob.video_id.in_(vids), DownloadJob.status.in_((JobStatus.queued, JobStatus.paused))
        )
    ):
        if jobs.compute_effective_cap(s, job.video_id) is None:
            jobs.cancel_job(s, job)


def apply_rule_patch(s: Session, user_id: int, channel_id: str, patch: ChannelRulePatch) -> ChannelRule:
    """Apply a patch. Raises ConfirmationRequired *before* mutating when a large backfill is not confirmed."""
    rule = s.scalar(select(ChannelRule).where(ChannelRule.user_id == user_id, ChannelRule.channel_id == channel_id))
    old_mode = ArchiveMode(rule.mode) if rule else ArchiveMode.off
    old_paused = rule.paused if rule else False
    new_mode = ArchiveMode(patch.mode) if patch.mode else old_mode
    new_paused = patch.paused if patch.paused is not None else old_paused

    if new_mode in BACKFILL_MODES and old_mode not in BACKFILL_MODES and not patch.confirm_large:
        est = estimate_channel(user_id, channel_id, patch.max_height if "max_height" in _set_fields(patch) else None)
        if est.requires_confirmation:
            raise ConfirmationRequired(est)

    created = rule is None
    if rule is None:
        rule = ChannelRule(user_id=user_id, channel_id=channel_id, mode=ArchiveMode.off)
        s.add(rule)
        s.flush()
    fields = _set_fields(patch)
    filters_before = (rule.include_shorts, rule.include_live, _aware(rule.published_after), rule.max_duration_s)
    if patch.include_shorts is not None:
        rule.include_shorts = patch.include_shorts
    if patch.include_live is not None:
        rule.include_live = patch.include_live
    if "published_after" in fields:
        rule.published_after = patch.published_after
    if "max_duration_s" in fields:
        rule.max_duration_s = patch.max_duration_s
    if patch.clear_max_height:
        rule.max_height = None
    elif "max_height" in fields:
        rule.max_height = patch.max_height
    filters_changed = filters_before != (
        rule.include_shorts,
        rule.include_live,
        _aware(rule.published_after),
        rule.max_duration_s,
    )

    was_active = old_mode != ArchiveMode.off and not old_paused
    will_be_active = new_mode != ArchiveMode.off and not new_paused
    rule.mode = new_mode
    rule.paused = new_paused
    if will_be_active and (not was_active or rule.activated_at is None):
        rule.activated_at = (
            datetime.now(UTC) if (old_mode == ArchiveMode.off or rule.activated_at is None) else rule.activated_at
        )
    needs_rescan = (
        (will_be_active and not was_active)
        or (new_mode in BACKFILL_MODES and old_mode not in BACKFILL_MODES)
        or (filters_changed and new_mode in BACKFILL_MODES)
    )
    if needs_rescan and new_mode in BACKFILL_MODES:
        rule.backfill_page_token = None
        rule.backfill_completed_at = None
    if was_active and not will_be_active:
        deactivate_rule_sources(s, rule)
    if created or will_be_active != was_active:
        rule.sync_status = SyncStatus.idle
    s.flush()
    return rule
