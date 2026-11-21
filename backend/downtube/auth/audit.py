"""Audit trail helper."""

from __future__ import annotations

from sqlalchemy.orm import Session

from downtube.models import AuditEvent


def audit(
    s: Session, action: str, user_id: int | None = None, detail: dict | None = None, ip: str | None = None
) -> None:
    s.add(AuditEvent(user_id=user_id, action=action, detail=detail, ip=ip))
