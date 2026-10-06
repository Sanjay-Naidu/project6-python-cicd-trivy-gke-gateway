# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/users.py - registration and login
# =============================================================================
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from medicart import audit
from medicart.models import Role, User
from medicart.security import DUMMY_HASH, hash_password, verify_password

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 10


class UserError(ValueError):
    """A problem the user can fix (shown back on the form)."""


def create_user(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
    role: Role = Role.PATIENT,
    source_ip: str | None = None,
) -> User:
    email = email.strip().lower()
    full_name = full_name.strip()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise UserError("Enter a valid email address.")
    if not full_name or len(full_name) > 120:
        raise UserError("Enter your full name.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    try:
        db.flush()  # assigns user.id; the UNIQUE constraint fires here
    except IntegrityError:
        db.rollback()
        # The unique index, not a SELECT-then-INSERT check, decides: two
        # replicas registering the same email at once cannot both succeed.
        raise UserError("An account with this email already exists.") from None
    audit.record(
        db,
        "user.registered",
        actor=user,
        entity="user",
        entity_id=user.id,
        detail={"role": role.value},
        source_ip=source_ip,
    )
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str, source_ip: str | None) -> User | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, DUMMY_HASH)  # same cost as a real check
        ok = False
    else:
        ok = verify_password(password, user.password_hash)

    audit.record(
        db,
        "auth.login_succeeded" if ok else "auth.login_failed",
        actor=user if ok else None,
        entity="user",
        entity_id=user.id if user else None,
        # Record the attempted email on failure (for spotting credential
        # stuffing), never the password.
        detail={} if ok else {"email": email.strip().lower()[:254]},
        source_ip=source_ip,
    )
    db.commit()
    return user if ok else None
