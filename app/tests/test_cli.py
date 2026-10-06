# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/tests/test_cli.py - operator CLI + logging setup
# =============================================================================
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from medicart import cli
from medicart.models import User
from medicart.observability import DropHealthChecks, JsonFormatter
from tests.conftest import PASSWORD, TEST_DATABASE_URL


def test_create_pharmacist(monkeypatch, db, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    argv = ["create-user", "--email", "Rx@Example.com", "--name", "Priya S", "--role", "pharmacist"]
    assert cli.main(argv, password=PASSWORD) == 0
    assert "Created pharmacist rx@example.com" in capsys.readouterr().out
    user = db.scalar(select(User).where(User.email == "rx@example.com"))
    assert user is not None and user.is_pharmacist

    assert cli.main(argv, password=PASSWORD) == 1  # duplicate
    assert "already exists" in capsys.readouterr().err


def test_create_user_password_prompt_mismatch(monkeypatch):
    answers = iter(["first-password", "second-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    assert cli.main(["create-user", "--email", "a@b.co", "--name", "A"]) == 1


def test_json_log_format():
    record = logging.LogRecord("medicart", logging.ERROR, __file__, 1, "boom %s", ("x",), None)
    entry = json.loads(JsonFormatter().format(record))
    assert entry["severity"] == "ERROR" and entry["message"] == "boom x"


def test_health_checks_are_not_access_logged():
    f = DropHealthChecks()

    def access(path: str) -> logging.LogRecord:
        args = ("10.0.0.1:5000", "GET", path, "1.1", 200)
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", args, None)

    assert not f.filter(access("/readyz"))
    assert f.filter(access("/cart"))
