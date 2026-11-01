"""Read-only readiness check. Startup never silently migrates an existing installation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from study_trail import db
from study_trail.platform import PURPOSES, bound, cipher

with db.engine.connect() as conn:
    if conn.dialect.name != "mysql" or not str(conn.exec_driver_sql("SELECT VERSION()").scalar()).startswith("8.4."):
        raise RuntimeError("This release requires MySQL 8.4")
    target = ScriptDirectory.from_config(Config(str(Path(__file__).parents[1] / "alembic.ini"))).get_current_head()
    if conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() != target:
        raise RuntimeError("Run the verified platform migration and bootstrap before starting this version.")
    for slot in ("limits", *("prompt:" + p for p in PURPOSES)):
        bound(conn, slot)
    cipher()
    if conn.scalar(sa.select(sa.func.count()).select_from(db.platform_configs).where(db.platform_configs.c.current_revision_id.is_(None))):
        raise RuntimeError("Platform configuration has no current revision")
    if conn.scalar(sa.select(sa.func.count()).select_from(db.runtime_locks)) != 258:
        raise RuntimeError("Runtime lock baseline is incomplete")
from study_trail.vectors import collection

collection()
print("MySQL schema, platform essentials, encryption key and Chroma collection are ready.")
