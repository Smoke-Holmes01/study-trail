"""Explicit fresh installation. Never reads, migrates or deletes the PostgreSQL stack."""
import argparse
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tools.local_runtime import LOCAL, ROOT, compose, protect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-login", required=True)
    args = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    infra, backend = LOCAL / "infra.env", LOCAL / "backend.env"
    if infra.exists() != backend.exists():
        raise RuntimeError("Partial environment exists; restore the matching files before installation")
    if not infra.exists():
        root, app = secrets.token_hex(24), secrets.token_hex(24)
        with infra.open("x", encoding="utf-8") as stream:
            stream.write(f"MYSQL_ROOT_PASSWORD={root}\nMYSQL_PASSWORD={app}\nMYSQL_PORT=53306\nCHROMA_PORT=58000\n")
        with backend.open("x", encoding="utf-8") as stream:
            stream.write(f"DATABASE_URL=mysql+pymysql://study_trail:{app}@127.0.0.1:53306/study_trail?charset=utf8mb4\nCURSOR_SECRET={secrets.token_hex(32)}\nCHROMA_HOST=127.0.0.1\nCHROMA_PORT=58000\nCHROMA_COLLECTION=study_trail_v004_chunks\n")
    protect(infra)
    protect(backend)
    compose("up", "-d", "--wait")
    import sqlalchemy as sa
    from dotenv import dotenv_values
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    engine = sa.create_engine(dotenv_values(backend)["DATABASE_URL"])
    try:
        with engine.connect() as conn:
            tables = set(sa.inspect(conn).get_table_names())
            head = ScriptDirectory.from_config(Config(str(ROOT / "backend/alembic.ini"))).get_current_head()
            current = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() if "alembic_version" in tables else None
            if tables - {"alembic_version"} and current != head:
                raise RuntimeError("Existing installation requires a verified backup and apply_upgrade.py; install never upgrades populated old schemas")
    finally:
        engine.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", check=True)
    subprocess.run([sys.executable, "tools/bootstrap_platform.py", "--admin-login", args.admin_login, "--generate-admin"], cwd=ROOT / "backend", check=True)
    subprocess.run([sys.executable, "tools/vector_index.py", "init", "--apply"], cwd=ROOT / "backend", check=True)
    subprocess.run([sys.executable, "tools/check_installation.py"], cwd=ROOT / "backend", check=True)


if __name__ == "__main__":
    main()
