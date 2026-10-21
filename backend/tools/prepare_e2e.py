"""Prepare only study_trail_e2e; credentials are test fixtures, never production defaults."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa

from study_trail.config import settings

source = sa.engine.make_url(settings().database_url)
name = "study_trail_e2e"
engine = sa.create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    if not conn.execute(sa.text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}).scalar():
        conn.exec_driver_sql("CREATE DATABASE study_trail_e2e")
env = {
    **os.environ,
    "DATABASE_URL": source.set(database=name).render_as_string(hide_password=False),
    "PLATFORM_KEYS": json.dumps({"test-v1": "dGVzdC1vbmx5LWtleS0zMi1ieXRlcy1sb25nISEhISE="}),
    "PLATFORM_KEY_ID": "test-v1",
    "STORAGE_ROOT": str(Path(__file__).parents[2] / ".local/e2e-storage"),
    "PYTHONUTF8": "1",
    "ALLOWED_ORIGINS": '["http://127.0.0.1:5174"]',
}
subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).parents[1], env=env, check=True
)
code = """from study_trail import db, core
from study_trail.bootstrap import initialize, refresh_builtin_guide
import sqlalchemy as sa
with db.engine.begin() as conn:
    actor=conn.scalar(sa.select(db.users.c.id).where(db.users.c.login_normalized=='e2e_admin'))
    if not actor:
        actor=db.insert(conn,db.users,login='e2e_admin',login_normalized='e2e_admin',display_name='测试管理员',role='admin',password_hash=core.password_hash.hash('e2e-admin-password'))['id']
    refresh_builtin_guide(conn,actor)
    initialize(conn)
    from tools.seed_test_platform import seed_test_platform
    seed_test_platform(conn)
"""
subprocess.run([sys.executable, "-c", code], env=env, cwd=Path(__file__).parents[1], check=True)
# The environment file is local-only and contains a URL credential. Do not print it.
target = Path(__file__).parents[2] / ".local/e2e-environment.json"
target.write_text(
    json.dumps(
        {
            key: env[key]
            for key in (
                "DATABASE_URL",
                "PLATFORM_KEYS",
                "PLATFORM_KEY_ID",
                "STORAGE_ROOT",
                "ALLOWED_ORIGINS",
                "PYTHONUTF8",
            )
        }
    ),
    encoding="utf-8",
)
target.chmod(0o600)
print("Isolated browser-test database ready.")
