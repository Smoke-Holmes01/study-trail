"""Platform configuration and privacy-preserving operational facts."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


def install(metadata):
    def column(name, kind=sa.Text, **kwargs):
        return sa.Column(name, kind, nullable=False, **kwargs)

    def ident():
        return column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            default=uuid4,
            server_default=sa.text("gen_random_uuid()"),
        )

    def timestamp(name="created_at"):
        return column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"))

    configs = sa.Table(
        "platform_configs",
        metadata,
        ident(),
        column("kind", sa.String(16)),
        column("stable_id", sa.String(80)),
        column("name", sa.String(80)),
        column("description", default="", server_default=""),
        column("enabled", sa.Boolean, default=False, server_default=sa.false()),
        sa.Column("purpose", sa.String(48)),
        column("current_revision_id", UUID(as_uuid=True)),
        column("lock_version", sa.Integer, default=1, server_default="1"),
        timestamp(),
        timestamp("updated_at"),
        sa.UniqueConstraint("kind", "stable_id"),
        sa.CheckConstraint("kind IN ('model','prompt','skill','mcp','limits')"),
        sa.CheckConstraint("lock_version > 0"),
    )
    sa.Table(
        "config_revisions",
        metadata,
        ident(),
        column("config_id", UUID(as_uuid=True)),
        column("revision_no", sa.Integer),
        column("payload", JSONB),
        column("credential_version_ids", JSONB, default=dict, server_default="{}"),
        column("content_hash", sa.String(64)),
        sa.Column("created_by", UUID(as_uuid=True)),
        timestamp(),
        sa.ForeignKeyConstraint(["config_id"], ["platform_configs.id"]),
        sa.UniqueConstraint("config_id", "revision_no"),
        sa.UniqueConstraint("config_id", "id"),
        sa.CheckConstraint("revision_no > 0"),
    )
    configs.append_constraint(
        sa.ForeignKeyConstraint(
            ["id", "current_revision_id"],
            ["config_revisions.config_id", "config_revisions.id"],
            name="fk_platform_current_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        )
    )
    sa.Index(
        "uq_active_prompt_purpose",
        configs.c.purpose,
        unique=True,
        postgresql_where=sa.text("kind = 'prompt' AND enabled"),
    )
    sa.Table(
        "credential_versions",
        metadata,
        ident(),
        column("config_id", UUID(as_uuid=True)),
        column("slot", sa.String(80)),
        column("version_no", sa.Integer),
        column("ciphertext", sa.LargeBinary),
        column("key_id"),
        sa.Column("created_by", UUID(as_uuid=True)),
        timestamp(),
        sa.ForeignKeyConstraint(["config_id"], ["platform_configs.id"]),
        sa.UniqueConstraint("config_id", "slot", "version_no"),
    )
    sa.Table(
        "platform_bindings",
        metadata,
        column("slot", sa.String(48), primary_key=True),
        column("config_id", UUID(as_uuid=True)),
        column("revision_id", UUID(as_uuid=True)),
        sa.Column("index_fingerprint", sa.String(64)),
        column("lock_version", sa.Integer, default=1, server_default="1"),
        timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["config_id", "revision_id"], ["config_revisions.config_id", "config_revisions.id"]
        ),
    )
    sa.Table(
        "resource_events",
        metadata,
        ident(),
        column("event_key", unique=True),
        column("owner_id", UUID(as_uuid=True)),
        column("resource_kind", sa.String(24)),
        column("resource_id", UUID(as_uuid=True)),
        column("action", default="created", server_default="created"),
        timestamp("occurred_at"),
        column("origin", default="live", server_default="live"),
    )
    sa.Table(
        "task_run_facts",
        metadata,
        column("task_id", UUID(as_uuid=True), primary_key=True),
        column("owner_id", UUID(as_uuid=True)),
        sa.Column("source_agent_id", UUID(as_uuid=True)),
        sa.Column("source_file_id", UUID(as_uuid=True)),
        sa.Column("file_extension", sa.String(8)),
        column("kind", sa.String(32)),
        sa.Column("action", sa.String(32)),
        timestamp("accepted_at"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        column("status", sa.String(16)),
        sa.Column("model_config_id", UUID(as_uuid=True)),
        sa.Column("safe_error_code", sa.String(80)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','stopped')"),
    )
    sa.Table(
        "invocation_attempts",
        metadata,
        ident(),
        column("operation_key"),
        column("attempt_no", sa.Integer),
        sa.Column("task_id", UUID(as_uuid=True)),
        column("actor_id", UUID(as_uuid=True)),
        column("purpose", sa.String(32)),
        column("category", sa.String(16)),
        sa.Column("model_kind", sa.String(16)),
        sa.Column("config_id", UUID(as_uuid=True)),
        sa.Column("revision_id", UUID(as_uuid=True)),
        sa.Column("tool_name", sa.String(200)),
        timestamp("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        column("status", sa.String(16)),
        sa.Column("safe_error_code", sa.String(80)),
        sa.UniqueConstraint("operation_key", "attempt_no"),
        sa.CheckConstraint("status IN ('running','succeeded','failed','stopped','unknown')"),
    )
    sa.Table(
        "admin_operation_logs",
        metadata,
        ident(),
        column("actor_id", UUID(as_uuid=True)),
        column("action", sa.String(80)),
        column("target_kind", sa.String(24)),
        sa.Column("target_id", UUID(as_uuid=True)),
        column("result", sa.String(16)),
        sa.Column("safe_error_code", sa.String(80)),
        column("request_id", UUID(as_uuid=True)),
        timestamp("occurred_at"),
    )
    sa.Table(
        "metric_coverage",
        metadata,
        column("metric_key", primary_key=True),
        timestamp("coverage_start"),
        column("history_complete", sa.Boolean),
        column("note_code"),
    )
    sa.Table(
        "migration_imports",
        metadata,
        ident(),
        column("source_key", unique=True),
        column("source_sha256", sa.String(64)),
        column("config_id", UUID(as_uuid=True)),
        column("revision_id", UUID(as_uuid=True)),
        timestamp("imported_at"),
        sa.ForeignKeyConstraint(
            ["config_id", "revision_id"], ["config_revisions.config_id", "config_revisions.id"]
        ),
    )
    for name, cols in {
        "resource_events": ["occurred_at", "owner_id"],
        "task_run_facts": ["accepted_at", "owner_id", "status"],
        "invocation_attempts": ["started_at", "task_id", "purpose"],
        "admin_operation_logs": ["occurred_at", "actor_id"],
    }.items():
        for col in cols:
            sa.Index(f"ix_{name}_{col}", metadata.tables[name].c[col])
