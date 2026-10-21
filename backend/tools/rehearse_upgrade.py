"""Dump the existing database, restore a dedicated copy, then verify upgrade and recovery.

Does not migrate the daily-use database. The PostgreSQL dump and storage copy are
retained under src/.local/backups; the report includes identifiers/counts/hashes only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from cryptography.fernet import Fernet

from study_trail.config import settings
from study_trail.platform import digest

ROOT = Path(__file__).parents[2]
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


def snapshot(engine):
    result = {}
    with engine.connect() as conn:
        excluded = {
            "alembic_version",
            "sessions",
            "platform_configs",
            "config_revisions",
            "credential_versions",
            "platform_bindings",
            "resource_events",
            "task_run_facts",
            "invocation_attempts",
            "admin_operation_logs",
            "metric_coverage",
            "migration_imports",
        }
        for name in sorted(set(sa.inspect(conn).get_table_names()) - excluded):
            columns = [
                c["name"]
                for c in sa.inspect(conn).get_columns(name)
                if c["name"] not in {"role", "enabled", "last_login_at", "index_fingerprint"}
            ]
            # Preserve all preexisting columns, including private text, as an in-memory hash only.
            keys = sa.inspect(conn).get_pk_constraint(name)["constrained_columns"]
            where = (
                " WHERE role='user'"
                if name == "users" and any(c["name"] == "role" for c in sa.inspect(conn).get_columns(name))
                else ""
            )
            rows = (
                conn.exec_driver_sql(
                    "SELECT "
                    + ",".join('"' + c + '"' for c in columns)
                    + ' FROM "'
                    + name
                    + '"'
                    + where
                    + " ORDER BY "
                    + ",".join('"' + c + '"' for c in keys)
                )
                .mappings()
                .all()
            )
            # Vector, bytes and timestamps have stable repr across the same DB driver.
            result[name] = {
                "count": len(rows),
                "sha256": digest(
                    json.loads(json.dumps([dict(r) for r in rows], default=str, sort_keys=True))
                ),
            }
    return result


def storage_manifest(root):
    import hashlib

    return (
        {
            str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in root.rglob("*")
            if file.is_file()
        }
        if root.exists()
        else {}
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-backup", type=Path)
    parser.add_argument("--restored-database")
    args = parser.parse_args()
    cfg = settings()
    source = sa.engine.make_url(cfg.database_url)
    if source.database != "study_trail":
        raise RuntimeError("Expected the daily-use study_trail database; refusing an ambiguous source.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.prepared_backup or ROOT / ".local/backups" / ("platform-" + stamp)
    if not target.resolve().is_relative_to((ROOT / ".local/backups").resolve()):
        raise RuntimeError("Backup must be inside the local backup directory.")
    if not args.prepared_backup:
        target.mkdir(parents=True, exist_ok=False)
        container = subprocess.check_output(
            [DOCKER, "compose", "ps", "-q", "db"], cwd=ROOT / "infra", text=True
        ).strip()
        if not container:
            raise RuntimeError("Database container is not running.")
    engine = sa.create_engine(source, connect_args={"connect_timeout": 5})
    with engine.connect() as conn:
        if conn.exec_driver_sql("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").scalar():
            raise RuntimeError(
                "Wait for accepted tasks to finish before taking a consistent database/storage backup."
            )
    before = snapshot(engine)
    dump = target / "database.dump"
    if not args.prepared_backup:
        with dump.open("wb") as stream:
            run(
                DOCKER,
                "exec",
                container,
                "pg_dump",
                "-U",
                source.username,
                "-Fc",
                source.database,
                stdout=stream,
            )
    elif not dump.is_file() or dump.stat().st_size == 0:
        raise RuntimeError("Prepared database dump is missing.")
    if cfg.storage_root.exists() and not (target / "storage").exists():
        shutil.copytree(cfg.storage_root, target / "storage")
    if storage_manifest(cfg.storage_root) != storage_manifest(target / "storage"):
        raise RuntimeError("Storage backup differs from the original.")
    shutil.copy2(Path(__file__).parents[1] / ".env", target / "backend.env")
    (target / "backend.env").chmod(0o600)
    name = args.restored_database or "study_trail_rehearsal_" + uuid4().hex[:12]
    if not __import__("re").fullmatch(r"study_trail_rehearsal_[a-z0-9_]+", name):
        raise RuntimeError("Unexpected rehearsal database name.")
    if not args.restored_database:
        admin = sa.create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.exec_driver_sql('CREATE DATABASE "' + name + '"')
        with dump.open("rb") as stream:
            run(
                DOCKER,
                "exec",
                "-i",
                container,
                "pg_restore",
                "--exit-on-error",
                "-U",
                source.username,
                "-d",
                name,
                stdin=stream,
            )
    copy_url = source.set(database=name)
    copy_engine = sa.create_engine(copy_url)
    restored = snapshot(copy_engine)
    if restored != before:
        raise RuntimeError("Restored backup differs from source; upgrade rehearsal stopped.")
    key_file = target / "rehearsal-key.json"
    key_ring = key_file.read_text(encoding="utf-8") if key_file.exists() else json.dumps({"rehearsal-v1": Fernet.generate_key().decode()})
    # Persist before the first import, so a failed/repeated rehearsal uses the same key.
    if not key_file.exists():
        key_file.write_text(key_ring, encoding="utf-8")
        key_file.chmod(0o600)
    env = {
        **os.environ,
        "DATABASE_URL": copy_url.render_as_string(hide_password=False),
        "STORAGE_ROOT": str(target / "storage"),
        "PLATFORM_KEY_ID": "rehearsal-v1",
        "PLATFORM_KEYS": key_ring,
        "PYTHONUTF8": "1",
    }
    run(sys.executable, "-m", "alembic", "upgrade", "head", cwd=ROOT / "backend", env=env)
    code = """from study_trail import db
from study_trail.bootstrap import initialize
from study_trail.platform import decrypt
import sqlalchemy as sa
with db.engine.begin() as conn: initialize(conn)
with db.engine.begin() as conn: initialize(conn)
with db.engine.connect() as conn:
    ids=conn.execute(sa.select(db.credential_versions.c.id)).scalars().all()
for ident in ids: decrypt(str(ident))
print('Repeated import and credential decryption verified.')
"""
    run(sys.executable, "-c", code, cwd=ROOT / "backend", env=env)
    after = snapshot(copy_engine)
    if after != before:
        (target / "mismatch.json").write_text(
            json.dumps({"changed_tables": [k for k in before if before[k] != after.get(k)]}), encoding="utf-8"
        )
        raise RuntimeError("Upgrade changed existing learning data.")
    report = {
        "source_database": source.database,
        "rehearsal_database": name,
        "backup": str(target),
        "restore_verified": True,
        "upgrade_preserves_data": True,
        "repeat_import_verified": True,
        "original_data": before,
        "storage_verified": True,
        "storage_files": len(storage_manifest(cfg.storage_root)),
        "table_count": len(sa.inspect(copy_engine).get_table_names()) - 1,
        "rehearsed_at": datetime.now(UTC).isoformat(),
    }
    (target / "rehearsal.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Rehearsal secrets need not outlive the copy. Retain its key locally for diagnosis/recovery.
    (target / "rehearsal-key.json").write_text(env["PLATFORM_KEYS"], encoding="utf-8")
    (target / "rehearsal-key.json").chmod(0o600)
    print("Migration rehearsal passed. Report:", target / "rehearsal.json")


if __name__ == "__main__":
    main()
