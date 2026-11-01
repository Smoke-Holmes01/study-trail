"""Current initialization must preserve configuration and existing telemetry."""
import sqlalchemy as sa
import pytest
from study_trail import db
from study_trail.bootstrap import initialize
from tools.seed_test_platform import seed_test_platform


def test_repeat_initialization_preserves_telemetry(student):
    tables = [db.resource_events, db.task_run_facts, db.metric_coverage]
    with db.engine.begin() as conn:
        before = [list(conn.execute(sa.select(t)).mappings()) for t in tables]
        initialize(conn)
        initialize(conn)
        assert [list(conn.execute(sa.select(t)).mappings()) for t in tables] == before


def test_test_seed_is_idempotent_and_does_not_resurrect():
    with db.engine.begin() as conn:
        before = conn.scalar(sa.select(sa.func.count()).select_from(db.config_revisions))
        seed_test_platform(conn)
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.config_revisions)) == before
        row = conn.execute(sa.select(db.platform_configs).where(db.platform_configs.c.stable_id == 'exa')).mappings().one()
        db.update(conn, db.platform_configs, row['id'], enabled=False, deleted_at=db.now())
        with pytest.raises(RuntimeError, match='deleted'):
            seed_test_platform(conn)
        assert db.one(conn, db.platform_configs, row['id'])['deleted_at'] is not None
