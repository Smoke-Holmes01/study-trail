"""Apply a MySQL upgrade only with a verified, unchanged coordinated backup."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from study_trail import db
from study_trail.config import settings
from tools.storage_maintenance import operator_lock, relational_snapshot, storage_manifest, chroma_snapshot
from study_trail import vectors
from tools.local_runtime import ROOT, LOCAL
from tools.manage_runtime import running

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--refresh-guide", action="store_true", help="Explicitly adopt this release's guide source")
    parser.add_argument("--admin-login", help="Existing administrator for explicit guide refresh")
    args = parser.parse_args()
    report = args.report.resolve()
    if not report.is_relative_to((LOCAL / "backups").resolve()):
        raise RuntimeError("Expected a local coordinated backup report")
    data = json.loads(report.read_text(encoding="utf-8"))
    with operator_lock():
        if not data.get("restore_verified") or running() or not (LOCAL / "maintenance").exists():
            raise RuntimeError("Verified recovery and stopped application services are required")
        if relational_snapshot(db.engine, legacy_types="snapshot_format" not in data) != data["mysql"] or storage_manifest(settings().storage_root) != data["storage"] or chroma_snapshot(vectors.client()) != data["chroma"]:
            raise RuntimeError("Source data changed after rehearsal; take a fresh backup")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", check=True)
        from study_trail.bootstrap import initialize
        with db.engine.begin() as conn:
            if args.refresh_guide:
                import sqlalchemy as sa
                from study_trail.bootstrap import refresh_builtin_guide
                actor = conn.scalar(sa.select(db.users.c.id).where(db.users.c.login_normalized == (args.admin_login or "").lower(), db.users.c.role == "admin"))
                if not actor:
                    raise RuntimeError("Guide refresh requires an existing --admin-login")
                refresh_builtin_guide(conn, actor)
            initialize(conn)
        subprocess.run([sys.executable, "tools/check_installation.py"], cwd=ROOT / "backend", check=True)
        print("MySQL upgrade completed; maintenance remains enabled until explicit resume.")
