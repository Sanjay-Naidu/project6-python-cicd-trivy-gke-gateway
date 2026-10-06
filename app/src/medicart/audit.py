# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/audit.py - append-only audit trail
# =============================================================================
# Every security- or patient-relevant action is written in the SAME database
# transaction as the change itself: either both commit or neither does, so
# the trail can never claim something happened that didn't (or miss
# something that did).
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from medicart.models import AuditEvent, User


def record(
    db: Session,
    action: str,
    *,
    actor: User | None,
    entity: str,
    entity_id: int | str | None = None,
    detail: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            action=action,
            actor_id=actor.id if actor else None,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            detail=detail or {},
            source_ip=source_ip,
        )
    )


def recent(db: Session, limit: int = 100) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .options(joinedload(AuditEvent.actor))
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))
