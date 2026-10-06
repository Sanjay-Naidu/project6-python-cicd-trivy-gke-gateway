# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/config.py - runtime settings from the environment
# =============================================================================
# Plain environment variables (12-factor), read once at startup. Secrets are
# never passed as env vars in the cluster: the GKE Secret Manager add-on mounts
# them as files, and the *_FILE variables point at those files. Env vars leak
# into `kubectl describe`, crash dumps and child processes; a tmpfs file
# readable only by the app's UID does not.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL

LOCAL_ENVIRONMENTS = {"local", "test"}


class ConfigError(RuntimeError):
    """A required setting is missing - fail at startup, not on first request."""


def _secret(name: str) -> str | None:
    """Read NAME_FILE (preferred) or NAME from the environment."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.environ.get(name)


@dataclass(frozen=True)
class Settings:
    environment: str
    version: str
    pod_name: str
    database_url: str
    session_secret: str
    cookie_secure: bool
    log_level: str
    metrics_port: int

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.environ.get("APP_ENVIRONMENT", "local")

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            password = _secret("DATABASE_PASSWORD")
            if not password:
                raise ConfigError("DATABASE_PASSWORD_FILE (or DATABASE_URL) must be set")
            # URL.create escapes special characters in the password, which
            # string formatting would not.
            database_url = URL.create(
                "postgresql+psycopg",
                username=os.environ.get("DATABASE_USER", "medicart"),
                password=password,
                host=os.environ.get("DATABASE_HOST", "localhost"),
                port=int(os.environ.get("DATABASE_PORT", "5432")),
                database=os.environ.get("DATABASE_NAME", "medicart"),
            ).render_as_string(hide_password=False)

        session_secret = _secret("SESSION_SECRET")
        if not session_secret:
            if environment not in LOCAL_ENVIRONMENTS:
                raise ConfigError("SESSION_SECRET_FILE must be set outside local/test")
            session_secret = "local-dev-only-not-a-secret"  # noqa: S105

        return cls(
            environment=environment,
            version=os.environ.get("APP_VERSION", "dev"),
            pod_name=os.environ.get("POD_NAME", os.environ.get("HOSTNAME", "local")),
            database_url=database_url,
            session_secret=session_secret,
            # PUBLIC_SCHEME is how USERS reach the site (the LB terminates TLS,
            # so the pod itself always speaks plain HTTP). With https, cookies
            # get the Secure flag and responses carry HSTS.
            cookie_secure=os.environ.get("PUBLIC_SCHEME", "http") == "https",
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            metrics_port=int(os.environ.get("METRICS_PORT", "9090")),
        )
