"""Apply only after a verified rehearsal whose source hashes still match.

The launcher must already be stopped. On failure maintenance remains enabled;
recover using the matching database.dump, storage and backend.env backup.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa

from study_trail import db
from tools.rehearse_upgrade import ROOT, snapshot, storage_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--admin-login", required=True)
    parser.add_argument("--generate-admin", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if db.engine.url.database != "study_trail" or not all(
        report.get(key) for key in ("restore_verified", "upgrade_preserves_data", "repeat_import_verified")
    ):
        raise RuntimeError("Expected the daily-use database and a successful rehearsal report.")
    backup = args.report.resolve().parent
    if (
        not backup.is_relative_to((ROOT / ".local/backups").resolve())
        or not (backup / "database.dump").is_file()
    ):
        raise RuntimeError("Verified backup is missing or outside the local backup directory.")
    if snapshot(db.engine) != report["original_data"]:
        raise RuntimeError("Original data changed after rehearsal. Create and verify a fresh backup first.")
    if not report.get("storage_verified") or storage_manifest(db.settings().storage_root) != storage_manifest(
        backup / "storage"
    ):
        raise RuntimeError("Storage changed after backup; take and verify a fresh backup first.")
    with db.engine.connect() as conn:
        if conn.exec_driver_sql("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").scalar():
            raise RuntimeError("Accepted tasks must finish before applying the upgrade.")
    maintenance = ROOT / ".local/maintenance"
    maintenance.write_text("platform migration", encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=ROOT / "backend", check=True
    )
    command = [sys.executable, "tools/bootstrap_platform.py", "--admin-login", args.admin_login]
    if args.generate_admin:
        command.append("--generate-admin")
    subprocess.run(command, env=env, cwd=ROOT / "backend", check=True)
    # The new key is now needed for the upgraded database; retain it next to the verified backup.
    shutil.copy2(ROOT / "backend/.env", backup / "backend-upgraded.env")
    (backup / "backend-upgraded.env").chmod(0o600)
    after = snapshot(db.engine)
    if after != report["original_data"]:
        raise RuntimeError("Post-upgrade learning-data verification failed; maintenance remains enabled.")
    result = {
        "migration": "0005",
        "table_count": len(sa.inspect(db.engine).get_table_names()) - 1,
        "verified_backup": str(backup),
        "original_learning_data_preserved": True,
        "admin_login": args.admin_login,
    }
    (backup / "applied.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    maintenance.unlink()
    print("Local upgrade completed; original learning data verified. Report:", backup / "applied.json")


if __name__ == "__main__":
    main()
