"""Synthetic current-format configuration for isolated regression and E2E only."""
import json
from pathlib import Path
import sqlalchemy as sa
from study_trail import db, platform

def seed_test_platform(conn):
    if conn.engine.url.database not in {"study_trail_test", "study_trail_e2e"}:
        raise RuntimeError("Synthetic fixtures are allowed only in isolated test databases")
    platform.lock(conn)
    fixtures = json.loads((Path(__file__).parents[1] / "tests/fixtures/platform.json").read_text(encoding="utf-8"))
    for fixture in fixtures:
        body = dict(fixture)
        kind, slot = body.pop("kind"), body.pop("slot")
        row = conn.execute(sa.select(db.platform_configs).where(db.platform_configs.c.kind == kind, db.platform_configs.c.stable_id == body["stable_id"])).mappings().first()
        if row is None:
            row = platform.create(conn, kind, body, None)
        if row["deleted_at"] is not None:
            raise RuntimeError("Test fixture was deleted; reset the isolated test database explicitly")
        if slot and not conn.scalar(sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.slot == slot)):
            platform.bind(conn, slot, row)
