"""Read-only readiness check. Startup never silently migrates an existing installation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from study_trail import db
from study_trail.platform import bound, cipher
from alembic.config import Config
from alembic.script import ScriptDirectory
from study_trail.platform import PURPOSES

with db.engine.connect() as conn:
    target = ScriptDirectory.from_config(Config(str(Path(__file__).parents[1] / "alembic.ini"))).get_current_head()
    if conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() != target:
        raise RuntimeError("Run the verified platform migration and bootstrap before starting this version.")
    for slot in ("default_generation", "embedding", "rerank", "limits", *("prompt:" + p for p in PURPOSES)):
        bound(conn, slot)
    cipher()
print("Schema, platform defaults and encryption key are ready.")
