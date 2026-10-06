# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/security.py - passwords, CSRF, client IP
# =============================================================================
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, status

# --- Password hashing ---------------------------------------------------------
# scrypt from the standard library: memory-hard (expensive to brute-force on
# GPUs) and one less third-party dependency for Trivy to track.
# N=2^15, r=8 costs 32 MiB and ~100 ms per hash: slow for an attacker, and
# fine for a login. The parameters are stored with each hash so they can be
# raised later without breaking existing users.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    # Constant-time comparison: no timing side channel on the digest.
    return hmac.compare_digest(actual, expected)


# A real hash of a random password, used to spend the same time on logins for
# unknown emails - otherwise response time reveals which emails exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# --- CSRF -----------------------------------------------------------------------
# SameSite=Lax cookies already stop most cross-site POSTs; a per-session token
# in every form is the second, independent layer.
def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(expected, submitted or ""):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing form token")


# --- Client IP behind the Google load balancer --------------------------------
def client_ip(request: Request) -> str:
    """The real client IP for audit records.

    Google's external Application Load Balancer appends two entries to
    X-Forwarded-For: '<client-ip>,<load-balancer-ip>'. Anything before those
    was sent by the client and can be forged, so the trustworthy value is
    the SECOND-TO-LAST entry, never the first.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= 2:
            return parts[-2]
    return request.client.host if request.client else "unknown"
