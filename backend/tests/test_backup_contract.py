"""Backup hashes retain compatibility with manifests written before schema upgrades."""
import json
import os
import subprocess
import sys
import sqlalchemy as sa
from study_trail import db
from tools.storage_maintenance import digest, normalized, relational_snapshot
from tools.local_runtime import ROOT


def test_mixed_daily_and_verification_profile_is_rejected_before_docker():
    for runtime, project in ((ROOT / ".local/v010-verify", "study-trail-v004"),
                             (ROOT / ".local/v004", "study-trail-v010-verify")):
        env = {**os.environ, "RUNTIME_ROOT": str(runtime), "STUDY_TRAIL_COMPOSE_PROJECT": project}
        check = subprocess.run([sys.executable, "-c", "from tools.local_runtime import LOCAL"], env=env,
                               cwd=ROOT / "backend", capture_output=True, text=True)
        assert check.returncode != 0
        assert "identities must both" in check.stderr


def test_legacy_snapshot_matches_typed_manifest_algorithm(student):
    expected = {}
    with db.engine.connect() as conn:
        for name in sorted(sa.inspect(conn).get_table_names()):
            table = db.metadata.tables.get(name)
            if table is None:
                table = sa.Table(name, sa.MetaData(), autoload_with=conn)
            rows = [normalized(dict(row)) for row in conn.execute(sa.select(table)).mappings()]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            expected[name] = {"count": len(rows), "sha256": digest(rows)}
    assert relational_snapshot(db.engine, legacy_types=True) == expected
    reflected = relational_snapshot(db.engine)
    assert reflected["users"]["count"] == 1
    # Reflection returns physical CHAR UUID and DATETIME values. Both formats
    # are supported explicitly; their hashes must not be silently interchanged.
    assert reflected["users"]["sha256"] != expected["users"]["sha256"]
