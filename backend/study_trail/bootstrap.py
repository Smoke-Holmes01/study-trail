"""One-time, idempotent import of legacy configuration and reliable existing metadata."""

import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from . import db, skills
from . import platform as p
from .config import settings


def initialize(conn, actor=None, *, test=False):
    p.lock(conn)
    cfg = settings()

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

    legacy = cfg.model_catalog()
    for ident, model in legacy.models.items():
        provider = legacy.providers[model.provider_id]
        caps = model.model_dump(exclude={"provider_id", "api_model_id", "display_name", "enabled"})
        body = dict(
            stable_id=ident,
            name=model.display_name,
            enabled=model.enabled,
            payload=dict(
                model_kind="generative",
                api_model_id=model.api_model_id,
                endpoint=cfg.resolve(provider.base_url),
                capabilities=caps,
                exposed=True,
            ),
            _secrets={"api_key": "test-secret" if test else cfg.resolve(provider.api_key)},
        )
        # Import manifest fingerprints public sources; no secret or secret hash is persisted.
        public_hash = p.digest([ident, model.model_dump(), provider.model_dump()])
        existing = (
            conn.execute(
                sa.select(db.migration_imports).where(db.migration_imports.c.source_key == "model:" + ident)
            )
            .mappings()
            .first()
        )
        if existing:
            if existing["source_sha256"] != public_hash:
                raise RuntimeError("Model import source changed: " + ident)
            row = db.one(conn, db.platform_configs, existing["config_id"])
        else:
            secret_values = body.pop("_secrets")
            row = p.create(conn, "model", {**body, "secret_updates": secret_values}, actor)
            conn.execute(
                db.migration_imports.insert().values(
                    source_key="model:" + ident,
                    source_sha256=public_hash,
                    config_id=row["id"],
                    revision_id=row["current_revision_id"],
                )
            )
        if (
            ident == legacy.default_model_id
            and not conn.execute(
                sa.select(db.platform_bindings.c.slot).where(
                    db.platform_bindings.c.slot == "default_generation"
                )
            ).first()
        ):
            p.bind(conn, "default_generation", row)
    for kind, ident in [("embedding", "BAAI/bge-m3"), ("rerank", "BAAI/bge-reranker-v2-m3")]:
        payload = dict(
            model_kind=kind, api_model_id=ident, endpoint="https://api.siliconflow.cn/v1", capabilities={}
        )
        if kind == "embedding":
            payload.update(
                dimensions=1024,
                model_revision="initial",
                normalization_version="dense-v1",
                chunking_version="v1",
            )
        key = "model:" + kind
        old = (
            conn.execute(sa.select(db.migration_imports).where(db.migration_imports.c.source_key == key))
            .mappings()
            .first()
        )
        if old:
            row = db.one(conn, db.platform_configs, old["config_id"])
        else:
            row = p.create(
                conn,
                "model",
                dict(
                    stable_id=kind,
                    name=ident,
                    enabled=True,
                    payload=payload,
                    secret_updates={"api_key": "test-secret" if test else cfg.siliconflow_api_key},
                ),
                actor,
            )
            conn.execute(
                db.migration_imports.insert().values(
                    source_key=key,
                    source_sha256=p.digest(payload),
                    config_id=row["id"],
                    revision_id=row["current_revision_id"],
                )
            )
        if not conn.execute(
            sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.slot == kind)
        ).first():
            p.bind(conn, kind, row)
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
    for ident, server in cfg.file_mcp_servers().items():
        payload = {
            k: v for k, v in server.items() if k not in {"name", "description", "enabled"} and v is not None
        }
        secret_values = {}
        for mapping_name, prefix in [("env", "env."), ("headers", "header.")]:
            mapping = payload.get(mapping_name, {})
            for name, value in list(mapping.items()):
                if any(x in name.lower() for x in ("authorization", "token", "secret", "password", "key")):
                    secret_values[prefix + name] = cfg.resolve(mapping.pop(name))
        key = "mcp:" + ident
        sha = p.digest(server)
        old = (
            conn.execute(sa.select(db.migration_imports).where(db.migration_imports.c.source_key == key))
            .mappings()
            .first()
        )
        if old:
            if old["source_sha256"] != sha:
                raise RuntimeError("MCP import source changed: " + ident)
        else:
            row = p.create(
                conn,
                "mcp",
                dict(
                    stable_id=ident,
                    name=server["name"],
                    description=server["description"][:500],
                    enabled=server["enabled"],
                    payload=payload,
                    secret_updates=secret_values,
                ),
                actor,
            )
            conn.execute(
                db.migration_imports.insert().values(
                    source_key=key,
                    source_sha256=sha,
                    config_id=row["id"],
                    revision_id=row["current_revision_id"],
                )
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
    fp = conn.scalar(
        sa.select(db.platform_bindings.c.index_fingerprint).where(db.platform_bindings.c.slot == "embedding")
    )
    legacy_models = set(conn.execute(sa.select(db.files.c.embedding_model_id).where(
        db.files.c.status == "ready", db.files.c.index_fingerprint.is_(None))).scalars())
    if legacy_models - {"BAAI/bge-m3"}:
        raise RuntimeError("Legacy embedding identity cannot be verified; initialization stopped.")
    conn.execute(
        db.files.update()
        .where(db.files.c.index_fingerprint.is_(None))
        .values(index_fingerprint=fp, updated_at=db.files.c.updated_at)
    )
    conn.execute(
        db.document_chunks.update()
        .where(db.document_chunks.c.index_fingerprint.is_(None))
        .values(index_fingerprint=fp, updated_at=db.document_chunks.c.updated_at)
    )
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
