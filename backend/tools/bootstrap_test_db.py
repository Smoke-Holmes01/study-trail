"""Create/migrate an isolated test database, never reset the development database."""

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parents[1]))
from study_trail.config import settings

url = sa.engine.make_url(settings().database_url)
if url.database != "study_trail":
    raise RuntimeError("Unexpected development database")
admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as conn:
    if not conn.execute(sa.text("SELECT 1 FROM pg_database WHERE datname='study_trail_test'")).scalar():
        conn.exec_driver_sql("CREATE DATABASE study_trail_test")
env = os.environ.copy()
env["DATABASE_URL"] = url.set(database="study_trail_test").render_as_string(hide_password=False)
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).parents[1], env=env, check=True
)
print("Isolated test database ready.")
