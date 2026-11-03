"""Create isolated MySQL regression/E2E databases; never reset the daily database."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from dotenv import dotenv_values

from study_trail.config import settings
from tools.local_runtime import LOCAL


def prepare(name="study_trail_test"):
    if name not in {"study_trail_test", "study_trail_e2e"}:
        raise RuntimeError("Only explicit isolated database names are allowed")
    url = sa.engine.make_url(settings().database_url)
    if url.get_backend_name() != "mysql" or url.host != "127.0.0.1":
        raise RuntimeError("Expected local MySQL deployment")
    infra = dotenv_values(LOCAL / "infra.env")
    admin = sa.create_engine(url.set(username="root", password=infra["MYSQL_ROOT_PASSWORD"], database=None), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
        conn.exec_driver_sql(f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'study_trail'@'%%'")
    admin.dispose()
    from study_trail.config import ENV_FILE
    env = {**os.environ, "STUDY_TRAIL_ENV_FILE": str(ENV_FILE), "RUNTIME_ROOT": str(LOCAL),
           "CHROMA_HOST": settings().chroma_host, "CHROMA_PORT": str(settings().chroma_port),
           "DATABASE_URL": url.set(database=name).render_as_string(hide_password=False),
           "CHROMA_COLLECTION": name + "_chunks", "PYTHONUTF8": "1"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).parents[1], env=env, check=True)
    subprocess.run([sys.executable, "tools/vector_index.py", "init", "--apply"], cwd=Path(__file__).parents[1], env=env, check=True)
    return env


if __name__ == "__main__":
    prepare()
    print("Isolated MySQL/Chroma test environment ready.")
