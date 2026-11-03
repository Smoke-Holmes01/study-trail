"""v0.1.0 ownership, immutable revisions and template lifecycle schema."""
import sqlalchemy as sa

from .mysql_types import JSON, UUID, UTCDateTime


def install(m, table, col, parent):
    table("task_role_runs", col("task_id", UUID(as_uuid=True)),
        col("role_config_id", UUID(as_uuid=True), fk="platform_configs.id"),
        col("revision_id", UUID(as_uuid=True), fk="config_revisions.id"),
        col("role", sa.String(24)), col("step_no", sa.Integer),
        col("status", sa.String(16), default="queued"), col("phase", sa.String(48), default="queued"),
        col("degradation", JSON, default=list), col("error_code", sa.String(80), nullable=True),
        col("started_at", UTCDateTime(), nullable=True), col("finished_at", UTCDateTime(), nullable=True),
        parent("tasks", "task_id"), sa.UniqueConstraint("task_id", "step_no"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','stopped','skipped')"),
        sa.CheckConstraint("step_no >= 0"))
    table("agent_templates", col("admin_id", UUID(as_uuid=True), fk="users.id"),
        col("name", sa.String(50)), col("description", sa.String(500), default=""),
        col("status", sa.String(16), default="draft"), col("published_version_id", UUID(as_uuid=True), nullable=True, fk="template_versions.id"),
        col("lock_version", sa.Integer, default=1),
        sa.CheckConstraint("status IN ('draft','published','offline','deleted')"))
    table("template_versions", col("template_id", UUID(as_uuid=True)),
        col("version_no", sa.Integer), col("status", sa.String(16), default="draft"),
        col("payload", JSON, default=dict), col("lock_version", sa.Integer, default=1),
        parent("agent_templates", "template_id"), sa.UniqueConstraint("template_id", "version_no"),
        sa.CheckConstraint("status IN ('draft','published')"))
    table("template_assets", col("template_id", UUID(as_uuid=True)),
        col("storage_key", sa.String(255), unique=True), col("sha256", sa.LargeBinary),
        col("filename", sa.String(255)), col("media_type", sa.String(100)), col("extension", sa.String(16)),
        col("byte_size", sa.BigInteger), col("status", sa.String(16), default="processing"),
        col("parsed_content", nullable=True), col("text_metadata", JSON, nullable=True),
        col("chunks", JSON, default=list), col("parse_version", sa.Integer, default=1),
        col("latest_task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL"),
        col("error_code", sa.String(80), nullable=True), parent("agent_templates", "template_id"),
        sa.CheckConstraint("status IN ('processing','ready','failed')"))
    table("template_version_assets", col("version_id", UUID(as_uuid=True)), col("asset_id", UUID(as_uuid=True)),
        col("position", sa.Integer), col("manifest", JSON, default=dict),
        parent("template_versions", "version_id"), parent("template_assets", "asset_id"),
        sa.UniqueConstraint("version_id", "asset_id"))
    table("template_adoptions", col("agent_id", UUID(as_uuid=True), unique=True),
        col("template_version_id", UUID(as_uuid=True), fk="template_versions.id"),
        parent("agents", "agent_id"))
    table("template_import_batches", col("adoption_id", UUID(as_uuid=True)),
        col("target_kb_id", UUID(as_uuid=True), nullable=True, fk="knowledge_bases.id", delete="SET NULL"),
        col("status", sa.String(20), default="processing"), col("revision", sa.Integer, default=1),
        col("error_code", sa.String(80), nullable=True), parent("template_adoptions", "adoption_id"))
    table("template_import_items", col("batch_id", UUID(as_uuid=True)), col("adoption_id", UUID(as_uuid=True)),
        col("asset_id", UUID(as_uuid=True), fk="template_assets.id"),
        col("target_kb_id", UUID(as_uuid=True)), col("file_id", UUID(as_uuid=True), nullable=True, fk="files.id", delete="SET NULL"),
        col("task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL"),
        col("status", sa.String(16), default="waiting"), col("attempt_no", sa.Integer, default=1),
        col("error_code", sa.String(80), nullable=True), parent("template_import_batches", "batch_id"),
        parent("template_adoptions", "adoption_id"), sa.UniqueConstraint("adoption_id", "asset_id", "target_kb_id"))
    table("template_source_mappings", col("adoption_id", UUID(as_uuid=True)),
        col("reference_key", sa.String(160)), col("source_id", UUID(as_uuid=True), fk="sources.id"),
        col("file_id", UUID(as_uuid=True), nullable=True, fk="files.id", delete="SET NULL"),
        col("asset_id", UUID(as_uuid=True), fk="template_assets.id"),
        col("status", sa.String(24), default="template_not_imported"),
        parent("template_adoptions", "adoption_id"), sa.UniqueConstraint("adoption_id", "reference_key"))
    table("exercise_revisions", col("exercise_id", UUID(as_uuid=True)), col("version", sa.Integer),
        col("payload", JSON), col("origin_kind", sa.String(24), default="generated"),
        col("editor_id", UUID(as_uuid=True), nullable=True, fk="users.id"),
        parent("exercises", "exercise_id"), sa.UniqueConstraint("exercise_id", "version"))
    table("exercise_plan_links", col("exercise_id", UUID(as_uuid=True)), col("plan_id", UUID(as_uuid=True)),
        col("stage_id", UUID(as_uuid=True), nullable=True), col("task_id", UUID(as_uuid=True), nullable=True),
        parent("exercises", "exercise_id"), parent("learning_plans", "plan_id"),
        sa.UniqueConstraint("exercise_id", "plan_id"))
    table("exercise_sources", col("revision_id", UUID(as_uuid=True)), col("source_id", UUID(as_uuid=True)),
        parent("exercise_revisions", "revision_id"), parent("sources", "source_id"),
        sa.UniqueConstraint("revision_id", "source_id"))
    table("learning_plan_revisions", col("plan_id", UUID(as_uuid=True)), col("content_version", sa.Integer),
        col("name_snapshot", sa.String(50)), col("payload", JSON), col("origin_kind", sa.String(24), default="generated"),
        col("editor_id", UUID(as_uuid=True), nullable=True, fk="users.id"),
        parent("learning_plans", "plan_id"), sa.UniqueConstraint("plan_id", "content_version"))
    for name in ("exercises", "learning_plans"):
        t = m.tables[name]
        t.append_column(col("adoption_id", UUID(as_uuid=True), nullable=True, fk="template_adoptions.id", delete="SET NULL"))
        t.append_column(col("origin_kind", sa.String(24), default="generated"))
        t.append_column(col("user_edited", sa.Boolean, default=False))
    m.tables["exercises"].append_column(col("content_version", sa.Integer, default=1))
    m.tables["exercises"].append_column(col("current_revision_id", UUID(as_uuid=True), nullable=True, fk="exercise_revisions.id", delete="SET NULL"))
    for name in ("messages", "tasks"):
        m.tables[name].append_column(col("exercise_revision_id", UUID(as_uuid=True), nullable=True, fk="exercise_revisions.id", delete="SET NULL"))
        m.tables[name].append_column(col("exercise_snapshot", JSON, nullable=True))
    m.tables["sources"].append_column(col("adoption_id", UUID(as_uuid=True), nullable=True, fk="template_adoptions.id", delete="SET NULL"))
    m.tables["sources"].append_column(col("reference_key", sa.String(160), nullable=True))
    m.tables["invocation_attempts"].append_column(col("role_run_id", UUID(as_uuid=True), nullable=True, fk="task_role_runs.id", delete="SET NULL"))
    for name in ("task_role_runs", "template_import_batches", "template_import_items"):
        t = m.tables[name]
        sa.Index("ix_" + name + "_status", t.c.status)
