"""Initialize platform configuration and an explicitly named administrator.

Reads existing environment values without replacing them. --generate-admin writes a
new random password only to the local bootstrap file, never stdout.
"""

import argparse
import getpass
import json
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dotenv import dotenv_values

from study_trail import core, db
from study_trail.bootstrap import initialize, refresh_builtin_guide
from study_trail.config import ENV_FILE, settings


def ensure_key():
    cfg = settings()
    if cfg.platform_keys:
        from study_trail.platform import cipher

        cipher()
        return
    # A missing key on an initialized database must never silently rotate it.
    with db.engine.connect() as conn:
        if conn.scalar(sa.select(sa.func.count()).select_from(db.credential_versions)):
            raise RuntimeError("Existing encrypted credentials require the original PLATFORM_KEYS.")
    values = dotenv_values(ENV_FILE)
    if "PLATFORM_KEYS" in values or "PLATFORM_KEYS" in os.environ:
        raise RuntimeError("PLATFORM_KEYS is present but invalid/empty; refusing to overwrite it.")
    key = Fernet.generate_key().decode()
    with ENV_FILE.open("a", encoding="utf-8") as stream:
        stream.write("\nPLATFORM_KEY_ID=local-v1\nPLATFORM_KEYS='" + json.dumps({"local-v1": key}) + "'\n")
    settings.cache_clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-login", default=os.environ.get("ADMIN_LOGIN"))
    parser.add_argument("--generate-admin", action="store_true")
    parser.add_argument("--refresh-guide", action="store_true", help="Explicitly restore the release guide; preserve its status and old revisions")
    args = parser.parse_args()
    ensure_key()
    account = args.admin_login
    if account and not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", account):
        raise RuntimeError("Administrator login must be 3–32 letters, digits or underscores.")
    password = None
    with db.engine.begin() as conn:
        existing = (
            conn.execute(sa.select(db.users).where(db.users.c.login_normalized == account.lower()))
            .mappings()
            .first()
            if account
            else None
        )
        if existing and existing["role"] != "admin":
            raise RuntimeError(
                "Login already belongs to an ordinary user; choose a different administrator login."
            )
        if account and not existing:
            password = (
                secrets.token_urlsafe(24)
                if args.generate_admin
                else os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Administrator password: ")
            )
            if not 8 <= len(password) <= 128:
                raise RuntimeError("Administrator password must contain 8–128 characters.")
            existing = db.insert(
                conn,
                db.users,
                login=account,
                login_normalized=account.lower(),
                display_name=account,
                password_hash=core.password_hash.hash(password),
                role="admin",
            )
        if args.refresh_guide:
            if not existing:
                raise RuntimeError("--refresh-guide requires --admin-login identifying an administrator")
            refresh_builtin_guide(conn, existing["id"])
        initialize(conn, actor=existing["id"] if existing else None)
    if password and args.generate_admin:
        target = Path(__file__).parents[2] / ".local/admin-bootstrap.json"
        # Exclusive creation prevents accidental overwriting of earlier login instructions.
        with target.open("x", encoding="utf-8") as stream:
            json.dump(
                {"login": account, "password": password, "url": "http://127.0.0.1:5173/admin/login"},
                stream,
                ensure_ascii=False,
                indent=2,
            )
        target.chmod(0o600)
        print("Administrator credentials saved locally:", target)
    print("Platform initialization verified; existing configuration was not overwritten.")


if __name__ == "__main__":
    main()
