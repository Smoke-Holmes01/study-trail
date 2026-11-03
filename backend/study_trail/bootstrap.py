"""Idempotent platform essentials; external services are configured by administrators."""

import json
from pathlib import Path

import sqlalchemy as sa

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
    for stable_id, name in p.ROLE_NAMES.items():
        if not conn.scalar(sa.select(db.platform_configs.c.id).where(db.platform_configs.c.kind == "role", db.platform_configs.c.stable_id == stable_id)):
            allowed = {kind: list(conn.scalars(sa.select(db.platform_configs.c.stable_id).where(
                db.platform_configs.c.kind == kind, db.platform_configs.c.enabled.is_(True), db.platform_configs.c.deleted_at.is_(None))))
                for kind in ("mcp", "skill")}
            p.create(conn, "role", {"stable_id": stable_id, "name": name, "enabled": True,
                "payload": {"body": f"负责{name}，仅完成受委派任务，依据实际来源回答；不进行独立知识正确性复核。",
                            "allowed_mcp_ids": allowed["mcp"], "allowed_skill_ids": allowed["skill"]}}, actor)
    row = imported(
        "limits:initial",
        "limits",
        dict(stable_id="runtime-limits", name="运行限制", enabled=True, payload=dict(p.DEFAULT_LIMITS)),
    )
    if not conn.execute(
        sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.slot == "limits")
    ).first():
        p.bind(conn, "limits", row)
    for key in ("resources", "tasks", "calls"):
        db.insert_once(conn, db.metric_coverage, dict(metric_key=key, history_complete=False, note_code='HISTORY_NOT_COLLECTED'), ['metric_key'])
