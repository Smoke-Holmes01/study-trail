"""Read-only readiness check. Startup never silently migrates an existing installation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from study_trail import db
from study_trail.platform import bound, cipher

with db.engine.connect() as conn:
    if conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() != "0005":
        raise RuntimeError("Run the verified platform migration and bootstrap before starting this version.")
    for slot in ("default_generation", "embedding", "rerank", "limits"):
        bound(conn, slot)
    cipher()
print("Schema, platform defaults and encryption key are ready.")
