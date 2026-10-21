"""Explicit synthetic provider fixtures, restricted to isolated test databases."""
import sqlalchemy as sa
from study_trail import db
from study_trail import platform as p
from study_trail.config import settings


def seed_test_platform(conn):
    if conn.engine.url.database not in {"study_trail_test", "study_trail_e2e"}:
        raise RuntimeError("Synthetic fixtures are allowed only in isolated test databases")
    p.lock(conn)
    cfg = settings()
    actor = None
    test = True
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
