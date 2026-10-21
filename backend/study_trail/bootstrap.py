"""Idempotent platform essentials; external services are configured by administrators."""

import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import db, skills
from . import platform as p


def refresh_builtin_guide(conn, actor):
    """Explicit release action; normal initialization never overwrites platform edits."""
    from .admin import audit
    user = db.one(conn, db.users, actor)
    if not user or user["role"] != "admin":
        raise RuntimeError("An administrator identity is required to refresh the built-in guide.")
    p.lock(conn)
    skill = skills.file_catalog()["study-trail-guide"]
    row = conn.execute(sa.select(db.platform_configs).where(
        db.platform_configs.c.kind == "skill", db.platform_configs.c.stable_id == "study-trail-guide"
    )).mappings().first()
    if not row:
        return
    payload = {
        "body": skill["body"], "description": skill["description"],
        "content_version": skill["version"], "default_enabled": False,
    }
    source = dict(stable_id=skill["id"], name=skill["name"], enabled=True,
                  description=skill["description"][:500], payload=payload)
    if row["deleted_at"] is not None:
        # Explicit upgrades can accept the new source without resurrecting a deleted guide.
        conn.execute(db.migration_imports.update().where(
            db.migration_imports.c.source_key == "skill:study-trail-guide"
        ).values(source_sha256=p.digest(source)))
        return
    current = db.one(conn, db.config_revisions, row["current_revision_id"])
    if current["payload"] != payload:
        current = p.revision(conn, row, payload, None, actor)
        audit(conn, actor, "builtin_guide_refresh", "skill", row["id"])
    conn.execute(db.migration_imports.update().where(
        db.migration_imports.c.source_key == "skill:study-trail-guide"
    ).values(source_sha256=p.digest(source), revision_id=current["id"]))


def initialize(conn, actor=None):
    p.lock(conn)

    def imported(key, kind, body):
        sha = p.digest(body)
        old = (
            conn.execute(sa.select(db.migration_imports).where(db.migration_imports.c.source_key == key))
            .mappings()
            .first()
        )
        if old:
            if old["source_sha256"] != sha:
                raise RuntimeError(
                    "Initialization source changed; use the administrator editor instead: " + key
                )
            return db.one(conn, db.platform_configs, old["config_id"])
        secrets = body.pop("_secrets", {})
        row = p.create(conn, kind, {**body, "secret_updates": secrets}, actor)
        conn.execute(
            db.migration_imports.insert().values(
                source_key=key, source_sha256=sha, config_id=row["id"], revision_id=row["current_revision_id"]
            )
        )
        return row

    prompts = json.loads(Path(__file__).with_name("initial_prompts.json").read_text(encoding="utf-8"))
    for purpose, body in prompts.items():
        row = imported(
            "prompt:" + purpose,
            "prompt",
            dict(
                stable_id=purpose.replace("_", "-"),
                name=purpose,
                purpose=purpose,
                payload={"purpose": purpose, "body": body},
            ),
        )
        slot = "prompt:" + purpose
        if row["deleted_at"] is not None:
            continue
        if not conn.execute(
            sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.slot == slot)
        ).first():
            row = db.update(conn, db.platform_configs, row["id"], enabled=True)
            p.bind(conn, slot, row)
    for ident, skill in skills.file_catalog().items():
        imported(
            "skill:" + ident,
            "skill",
            dict(
                stable_id=ident,
                name=skill["name"],
                enabled=True,
                description=skill["description"][:500],
                payload={
                    "body": skill["body"],
                    "description": skill["description"],
                    "content_version": skill["version"],
                    "default_enabled": skill["default_enabled"],
                },
            ),
        )
    row = imported(
        "limits:initial",
        "limits",
        dict(stable_id="runtime-limits", name="运行限制", enabled=True, payload=dict(p.DEFAULT_LIMITS)),
    )
    if not conn.execute(
        sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.slot == "limits")
    ).first():
        p.bind(conn, "limits", row)
    from .telemetry import RESOURCE_TABLES

    for name in sorted(RESOURCE_TABLES):
        table = db.metadata.tables[name]
        for row in conn.execute(sa.select(table)).mappings():
            if name == "users" and row["role"] != "user":
                continue
            conn.execute(
                insert(db.resource_events)
                .values(
                    event_key=f"{name}:{row['id']}:created",
                    resource_kind=name,
                    resource_id=row["id"],
                    owner_id=row.get("owner_id", row["id"]),
                    occurred_at=row["created_at"],
                    origin="backfill",
                )
                .on_conflict_do_nothing(index_elements=["event_key"])
            )
    from .telemetry import inserted

    for task in conn.execute(sa.select(db.tasks).where(db.tasks.c.kind != "storage_cleanup")).mappings():
        if not conn.execute(
            sa.select(db.task_run_facts.c.task_id).where(db.task_run_facts.c.task_id == task["id"])
        ).first():
            inserted(conn, db.tasks, task)
            from .telemetry import task_changed

            task_changed(conn, task)
    for key in ("resources", "tasks", "calls"):
        conn.execute(
            insert(db.metric_coverage)
            .values(metric_key=key, history_complete=False, note_code="HISTORY_NOT_COLLECTED")
            .on_conflict_do_nothing(index_elements=["metric_key"])
        )
