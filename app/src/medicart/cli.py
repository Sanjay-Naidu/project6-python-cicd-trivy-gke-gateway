# =============================================================================
# Project : Project 6 - Python (FastAPI) App CI/CD on Google GKE
# Author  : Sanjay Naidu
# File    : app/src/medicart/cli.py - operator commands (create a pharmacist)
# =============================================================================
# Pharmacist accounts can't be self-registered (the public form only creates
# patients). An operator creates them from inside the cluster:
#
#   kubectl -n medicart-prod exec -it deploy/medicart -c medicart -- \
#     python -m medicart.cli create-user --role pharmacist \
#       --email pharmacist@example.com --name "Priya Sharma"
#
# The password is read with getpass (never echoed, never on the command
# line, so it doesn't land in shell history or `ps` output).
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from medicart.config import Settings
from medicart.models import Role
from medicart.users import UserError, create_user


def main(argv: list[str] | None = None, password: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medicart.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user", help="create a patient or pharmacist account")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", choices=[r.value for r in Role], default=Role.PATIENT.value)
    args = parser.parse_args(argv)

    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1

    engine = create_engine(Settings.from_env().database_url)
    try:
        with Session(engine) as db:
            user = create_user(
                db,
                email=args.email,
                full_name=args.name,
                password=password,
                role=Role(args.role),
                source_ip="cli",
            )
            print(f"Created {user.role} {user.email} (id {user.id})")
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
