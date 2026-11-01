from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMBLOB, BINARY
from .mysql_types import JSON, UUID, UTCDateTime, UUIDList

from .config import settings

metadata = sa.MetaData()


def now():
    return datetime.now(timezone.utc)


def table(name, *cols, owner=True, **kw):
    common = [sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid4)]
    if owner:
        common += [
            sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.UniqueConstraint("id", "owner_id"),
        ]
    common += [
        sa.Column("created_at", UTCDateTime(), nullable=False, default=now),
        sa.Column("updated_at", UTCDateTime(), nullable=False, default=now, onupdate=now),
    ]
    return sa.Table(name, metadata, *common, *cols, **kw)


def col(name, typ=sa.Text, nullable=False, default=None, fk=None, delete=None, unique=False):
    args = [name, typ]
    if fk:
        args.append(
            sa.ForeignKey(fk, ondelete=delete, use_alter=True, name=f"fk_{name}_{fk.replace('.', '_')}")
        )
    return sa.Column(*args, nullable=nullable, default=default, unique=unique)


def parent(parent_name, local_name, *extra):
    names = [local_name, "owner_id", *extra]
    return sa.ForeignKeyConstraint(
        names, [f"{parent_name}.{n}" for n in ["id", "owner_id", *extra]], ondelete="CASCADE"
    )


users = table(
    "users",
    col("login", sa.String(32)),
    col("login_normalized", sa.String(32), unique=True),
    col("password_hash"),
    col("display_name", sa.String(32)),
    col("theme", sa.String(8), default="light"),
    col("auth_version", sa.Integer, default=1),
    col("role", sa.String(8), default="user"),
    col("enabled", sa.Boolean, default=True),
    col("last_login_at", UTCDateTime(), nullable=True),
    sa.CheckConstraint("role IN ('user','admin')"),
    sa.CheckConstraint("theme IN ('light','dark')"),
    sa.CheckConstraint("REGEXP_LIKE(login_normalized, '^[a-z0-9_]{3,32}$', 'c')"),
    owner=False,
)
sessions = table(
    "sessions",
    col("realm", sa.String(8), default="user"),
    sa.CheckConstraint("realm IN ('user','admin')"),    col("token_hash", sa.LargeBinary, unique=True),
    col("csrf_token", sa.String(64)),
    col("auth_version", sa.Integer),
    col("expires_at", UTCDateTime()),
    col("revoked_at", UTCDateTime(), nullable=True),
    col("last_seen_at", UTCDateTime(), default=now),
)
agents = table(
    "agents",
    col("name", sa.String(50)),
    col("description", sa.String(500), default=""),
    col("system_prompt"),
    col("model_id", sa.String(200)),
    col("config_version", sa.Integer, default=1),
    col("mcp_server_ids", JSON, default=list),
    col("skill_ids", JSON, default=list),
)
knowledge_bases = table("knowledge_bases", col("name", sa.String(50)))
agent_knowledge_bases = sa.Table(
    "agent_knowledge_bases",
    metadata,
    col("agent_id", UUID(as_uuid=True)),
    col("knowledge_base_id", UUID(as_uuid=True)),
    col("owner_id", UUID(as_uuid=True)),
    col("created_at", UTCDateTime(), default=now),
    sa.PrimaryKeyConstraint("agent_id", "knowledge_base_id"),
    parent("agents", "agent_id"),
    parent("knowledge_bases", "knowledge_base_id"),
)
conversations = table(
    "conversations",
    col("agent_id", UUID(as_uuid=True)),
    col("name", sa.String(50), default="新对话"),
    col("next_sequence_no", sa.BigInteger, default=1),
    col("has_images", sa.Boolean, default=False),
    col("last_accessed_at", UTCDateTime(), nullable=True),
    parent("agents", "agent_id"),
    sa.UniqueConstraint("id", "owner_id", "agent_id"),
)
messages = table(
    "messages",
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("sequence_no", sa.BigInteger),
    col("role", sa.String(16)),
    col("origin", sa.String(20)),
    col("content_text", default=""),
    col("skill", JSON, nullable=True),
    col("user_message_id", UUID(as_uuid=True), nullable=True, fk="messages.id", delete="CASCADE"),
    col("task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL", unique=True),
    col("response_status", sa.String(16), nullable=True),
    col("answer_exercise_id", UUID(as_uuid=True), nullable=True, fk="exercises.id", delete="SET NULL"),
    parent("conversations", "conversation_id", "agent_id"),
    sa.UniqueConstraint("conversation_id", "sequence_no"),
    sa.UniqueConstraint("id", "owner_id", "agent_id", "conversation_id"),
    sa.CheckConstraint("role IN ('user','assistant')"),
)
attachments = table(
    "attachments",
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("state", sa.String(12), default="staged"),
    col("original_name", sa.String(255)),
    col("storage_key", unique=True),
    col("media_type", sa.String(50)),
    col("byte_size", sa.BigInteger),
    col("width", sa.Integer),
    col("height", sa.Integer),
    col("sha256", sa.LargeBinary),
    col("expires_at", UTCDateTime(), nullable=True),
    parent("conversations", "conversation_id", "agent_id"),
    sa.UniqueConstraint("id", "owner_id", "agent_id", "conversation_id"),
    sa.CheckConstraint("state IN ('staged','bound')"),
    sa.CheckConstraint("byte_size > 0 AND byte_size <= 10485760"),
)
message_attachments = sa.Table(
    "message_attachments",
    metadata,
    col("message_id", UUID(as_uuid=True)),
    col("attachment_id", UUID(as_uuid=True), unique=True),
    col("owner_id", UUID(as_uuid=True)),
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("position", sa.SmallInteger),
    col("created_at", UTCDateTime(), default=now),
    sa.PrimaryKeyConstraint("message_id", "attachment_id"),
    sa.UniqueConstraint("message_id", "position"),
    parent("messages", "message_id", "agent_id", "conversation_id"),
    parent("attachments", "attachment_id", "agent_id", "conversation_id"),
    sa.CheckConstraint("position >= 0 AND position <= 5"),
)
files = table(
    "files",
    col("knowledge_base_id", UUID(as_uuid=True)),
    col("original_name", sa.String(255)),
    col("extension"),
    col("media_type"),
    col("storage_key", unique=True),
    col("sha256", sa.LargeBinary),
    col("byte_size", sa.BigInteger),
    col("status", sa.String(16), default="processing"),
    col("parse_version", sa.Integer, default=1),
    col("text_snapshot", nullable=True),
    col("text_metadata", JSON, nullable=True),
    col("embedding_model_id", nullable=True),
    col("indexed_at", UTCDateTime(), nullable=True),
    col("error_code", nullable=True),
    col("error_message", nullable=True),
    parent("knowledge_bases", "knowledge_base_id"),
    sa.UniqueConstraint("id", "owner_id", "knowledge_base_id"),
    sa.CheckConstraint("status IN ('processing','ready','failed')"),
    sa.CheckConstraint("byte_size > 0 AND byte_size <= 52428800"),
)
document_chunks = table(
    "document_chunks",
    col("knowledge_base_id", UUID(as_uuid=True)),
    col("file_id", UUID(as_uuid=True)),
    col("parse_version", sa.Integer),
    col("chunk_index", sa.Integer),
    col("content_text"),
    col("char_start", sa.BigInteger),
    col("char_end", sa.BigInteger),
    col("locator", JSON),
    parent("files", "file_id", "knowledge_base_id"),
    sa.UniqueConstraint("file_id", "parse_version", "chunk_index"),
    sa.CheckConstraint("char_start >= 0 AND char_end > char_start"),
)
sources = table(
    "sources",
    col("kind", sa.String(12)),
    col("file_id", UUID(as_uuid=True), nullable=True, fk="files.id", delete="SET NULL"),
    col("chunk_id", UUID(as_uuid=True), nullable=True, fk="document_chunks.id", delete="SET NULL"),
    col("title_snapshot"),
    col("original_file_id", UUID(as_uuid=True), nullable=True),
    col("url_snapshot", nullable=True),
    col("excerpt_snapshot"),
    col("locator_snapshot", JSON, nullable=True),
    col("captured_at", UTCDateTime(), default=now),
    sa.CheckConstraint("kind IN ('file','web')"),
)
learning_plans = table(
    "learning_plans",
    col("agent_id", UUID(as_uuid=True)),
    col("name", sa.String(50)),
    col("content", JSON),
    col("content_version", sa.Integer, default=1),
    col(
        "source_conversation_id", UUID(as_uuid=True), nullable=True, fk="conversations.id", delete="SET NULL"
    ),
    col("source_message_id", UUID(as_uuid=True), nullable=True, fk="messages.id", delete="SET NULL"),
    col("last_updated_by_task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL"),
    parent("agents", "agent_id"),
)
message_sources = sa.Table(
    "message_sources",
    metadata,
    col("message_id", UUID(as_uuid=True)),
    col("source_id", UUID(as_uuid=True)),
    col("owner_id", UUID(as_uuid=True)),
    col("position", sa.Integer),
    col("citation_spans", JSON, default=list),
    col("created_at", UTCDateTime(), default=now),
    sa.PrimaryKeyConstraint("message_id", "source_id"),
    sa.UniqueConstraint("message_id", "position"),
    parent("messages", "message_id"),
    parent("sources", "source_id"),
)
plan_sources = sa.Table(
    "plan_sources",
    metadata,
    col("plan_id", UUID(as_uuid=True)),
    col("source_id", UUID(as_uuid=True)),
    col("owner_id", UUID(as_uuid=True)),
    col("position", sa.Integer),
    col("content_version", sa.Integer),
    col("created_at", UTCDateTime(), default=now),
    sa.PrimaryKeyConstraint("plan_id", "source_id"),
    sa.UniqueConstraint("plan_id", "position"),
    parent("learning_plans", "plan_id"),
    parent("sources", "source_id"),
)
exercises = table(
    "exercises",
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("message_id", UUID(as_uuid=True)),
    col("position", sa.Integer),
    col("question_text"),
    col("provenance", sa.String(16)),
    col("source_ids", JSON, default=list),
    col("known_answer_snapshot", JSON, nullable=True),
    parent("messages", "message_id", "agent_id", "conversation_id"),
    sa.UniqueConstraint("message_id", "position"),
    sa.CheckConstraint("provenance IN ('original','adapted','generated')"),
)
tasks = table(
    "tasks",
    col("kind", sa.String(24)),
    col("action", sa.String(24), nullable=True),
    col("agent_id", UUID(as_uuid=True), nullable=True, fk="agents.id", delete="CASCADE"),
    col("conversation_id", UUID(as_uuid=True), nullable=True, fk="conversations.id", delete="CASCADE"),
    col("user_message_id", UUID(as_uuid=True), nullable=True, fk="messages.id", delete="CASCADE"),
    col("assistant_message_id", UUID(as_uuid=True), nullable=True, fk="messages.id", delete="SET NULL"),
    col("file_id", UUID(as_uuid=True), nullable=True, fk="files.id", delete="SET NULL"),
    col("target_plan_id", UUID(as_uuid=True), nullable=True, fk="learning_plans.id", delete="SET NULL"),
    col("base_plan_version", sa.Integer, nullable=True),
    col("attempt_no", sa.Integer, default=1),
    col("previous_task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL"),
    col("status", sa.String(16), default="queued"),
    col("cancel_requested", sa.Boolean, default=False),
    col("revision", sa.BigInteger, default=1),
    col("input_snapshot", JSON, default=dict),
    col("config_snapshot", JSON, default=dict),
    col("result_snapshot", JSON, default=dict),
    col("phase", default="queued"),
    col("preview_text", default=""),
    col("error_code", nullable=True),
    col("error_message", nullable=True),
    col("lease_owner", nullable=True),
    col("claim_token", UUID(as_uuid=True), nullable=True, unique=True),
    col("timeout_seconds", sa.Integer, default=300),
    col("queued_at", UTCDateTime(), default=now),
    col("started_at", UTCDateTime(), nullable=True),
    col("deadline_at", UTCDateTime(), nullable=True),
    col("lease_expires_at", UTCDateTime(), nullable=True),
    col("heartbeat_at", UTCDateTime(), nullable=True),
    col("finished_at", UTCDateTime(), nullable=True),
    col("next_attempt_at", UTCDateTime(), nullable=True),
    sa.CheckConstraint("status IN ('queued','running','succeeded','failed','stopped')"),
    sa.CheckConstraint("attempt_no >= 1"),
)
conversation_summaries = table(
    "conversation_summaries",
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("text"),
    col("through_sequence_no", sa.BigInteger),
    col("covered_user_message_ids", UUIDList(), default=list),
    col("model_id"),
    col("config_version", sa.Integer),
    col("created_by_task_id", UUID(as_uuid=True), nullable=True, fk="tasks.id", delete="SET NULL"),
    sa.UniqueConstraint("conversation_id", "through_sequence_no"),
    parent("conversations", "conversation_id", "agent_id"),
)
idempotency_records = table(
    "idempotency_records",
    col("key", sa.String(128)),
    col("method"),
    col("path"),
    col("request_fingerprint", sa.LargeBinary),
    col("response_status", sa.Integer),
    col("response_snapshot", JSON),
    col("resource_refs", JSON, default=list),
    col("resource_kind", nullable=True),
    col("resource_id", UUID(as_uuid=True), nullable=True),
    sa.UniqueConstraint("owner_id", "key"),
)

tasks.append_column(sa.Column("active_conversation", sa.String(32), sa.Computed("CASE WHEN kind = 'chat' AND status IN ('queued','running') THEN conversation_id ELSE NULL END")))
sa.Index("uq_active_chat", tasks.c.active_conversation, unique=True)
sa.Index("uq_message_attempt", tasks.c.user_message_id, tasks.c.attempt_no, unique=True)
sa.Index("ix_tasks_queue", tasks.c.status, tasks.c.next_attempt_at, tasks.c.queued_at)
sa.Index("ix_tasks_lease", tasks.c.lease_expires_at)
conversations.append_column(sa.Column("recent_at", UTCDateTime(), sa.Computed("COALESCE(last_accessed_at, created_at)")))
sa.Index("ix_recent_conversations", conversations.c.agent_id, conversations.c.recent_at, conversations.c.id)
for t in list(metadata.tables.values()):
    if "owner_id" in t.c:
        sa.Index(f"ix_{t.name}_owner", t.c.owner_id)
    for c in t.c:
        if c.foreign_keys and c.name != "owner_id":
            sa.Index(f"ix_{t.name}_{c.name}", c)

engine = sa.create_engine(settings().database_url, pool_pre_ping=True, pool_size=8, max_overflow=8, isolation_level="READ COMMITTED")


@sa.event.listens_for(engine, "connect")
def utc_connection(connection, record):
    with connection.cursor() as cursor:
        cursor.execute("SET time_zone = '+00:00'")

# Named database checks supplement API validation, including internal worker writes.
CHECKS = {
    "users": {
        "display_name": "CHAR_LENGTH(TRIM(display_name)) BETWEEN 1 AND 32",
        "auth_version": "auth_version > 0",
    },
    "sessions": {"token_hash": "octet_length(token_hash) = 32", "csrf": "REGEXP_LIKE(csrf_token, '^[0-9a-f]{64}$', 'c')"},
    "agents": {
        "name": "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 50",
        "prompt": "CHAR_LENGTH(TRIM(system_prompt)) BETWEEN 1 AND 10000",
        "version": "config_version > 0",
    },
    "knowledge_bases": {"name": "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 50"},
    "conversations": {"name": "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 50", "sequence": "next_sequence_no > 0"},
    "messages": {
        "sequence": "sequence_no > 0",
        "origin": "origin IN ('user_input','answer_request','assistant')",
        "response": "(role = 'user' AND response_status IS NULL) OR (role = 'assistant' AND response_status IN ('queued','running','succeeded','failed','stopped'))",
    },
    "attachments": {
        "dimensions": "width > 0 AND height > 0",
        "expiry": "(state = 'staged' AND expires_at IS NOT NULL) OR (state = 'bound' AND expires_at IS NULL)",
    },
    "files": {"version": "parse_version > 0"},
    "document_chunks": {
        "index": "chunk_index >= 0 AND parse_version > 0",
        "text": "CHAR_LENGTH(TRIM(content_text)) > 0",
    },
    "sources": {
        "title": "CHAR_LENGTH(TRIM(title_snapshot)) > 0",
        "url": "(kind = 'file' AND url_snapshot IS NULL) OR (kind = 'web' AND REGEXP_LIKE(url_snapshot, '^https?://', 'c'))",
    },
    "exercises": {
        "position": "position >= 0",
        "question": "CHAR_LENGTH(TRIM(question_text)) > 0",
        "source_array": "JSON_TYPE(source_ids) = 'ARRAY'",
    },
    "learning_plans": {"name": "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 50", "version": "content_version > 0"},
    "tasks": {
        "kind": "kind IN ('chat','prompt_generate','prompt_polish','file_process','storage_cleanup')",
        "action": "action IS NULL OR action IN ('plan_create','plan_update','exercise','answer','tutor','feedback','clarify')",
        "revision": "revision > 0 AND timeout_seconds > 0",
    },
    "conversation_summaries": {"text": "CHAR_LENGTH(TRIM(text)) BETWEEN 1 AND 1200"},
    "message_sources": {"position": "position >= 0"},
    "plan_sources": {"position": "position >= 0 AND content_version > 0"},
}
for table_name, rules in CHECKS.items():
    for name, expression in rules.items():
        metadata.tables[table_name].append_constraint(
            sa.CheckConstraint(expression, name=f"ck_{table_name}_{name}")
        )

from .platform_schema import install as install_platform

install_platform(metadata)
for _name in ("platform_configs", "config_revisions", "credential_versions", "platform_bindings",
              "resource_events", "task_run_facts", "invocation_attempts", "admin_operation_logs",
              "metric_coverage", "migration_imports"):
    globals()[_name] = metadata.tables[_name]
for _table in (files, document_chunks):
    _table.append_column(col("index_fingerprint", sa.String(64), nullable=_table is files))

runtime_locks = sa.Table("runtime_locks", metadata,
    sa.Column("scope", sa.String(16), primary_key=True),
    sa.Column("bucket", sa.SmallInteger, primary_key=True))
vector_generations = sa.Table("vector_generations", metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid4),
    sa.Column("owner_id", UUID(as_uuid=True), nullable=False),
    sa.Column("file_id", UUID(as_uuid=True), nullable=False),
    sa.Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    sa.Column("task_id", UUID(as_uuid=True)),
    sa.Column("claim_token", UUID(as_uuid=True)),
    sa.Column("parse_version", sa.Integer, nullable=False),
    sa.Column("index_fingerprint", sa.String(64), nullable=False),
    sa.Column("expected_count", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("state", sa.String(12), nullable=False, default="staging"),
    sa.Column("created_at", UTCDateTime(), nullable=False, default=now),
    sa.Column("retired_at", UTCDateTime()),
    sa.Column("next_cleanup_at", UTCDateTime()),
    sa.Column("cleanup_attempts", sa.Integer, nullable=False, default=0),
    sa.Column("cleanup_claim", UUID(as_uuid=True)),
    sa.Column("cleanup_lease_at", UTCDateTime()),
    sa.Column("last_error", sa.String(80)),
    sa.CheckConstraint("state IN ('staging','active','retired')", name="ck_vector_generations_state"))
sa.Index("ix_vector_cleanup", vector_generations.c.state, vector_generations.c.next_cleanup_at)
files.append_column(sa.Column("active_generation_id", UUID(as_uuid=True), sa.ForeignKey("vector_generations.id", name="fk_files_active_generation")))

# Explicit capacities and collations avoid MySQL TEXT indexes and silent truncation.
_INDEXED_TEXT = {"storage_key": 255, "event_key": 160, "operation_key": 160,
                 "metric_key": 80, "source_key": 255, "model_id": 200,
                 "key_id": 80, "resource_kind": 48, "media_type": 100, "extension": 16,
                 "safe_error_code": 80, "error_code": 80, "phase": 48,
                 "lease_owner": 80, "origin": 24, "action": 80, "note_code": 100}
for t in metadata.tables.values():
    t.dialect_options["mysql"].update(engine="InnoDB", charset="utf8mb4", collate="utf8mb4_0900_bin")
    for field in t.c:
        if isinstance(field.type, sa.Text):
            field.type = sa.String(_INDEXED_TEXT[field.name]) if field.name in _INDEXED_TEXT else LONGTEXT()
        if isinstance(field.type, sa.LargeBinary):
            field.type = BINARY(32) if field.name in {"token_hash", "sha256", "request_fingerprint"} else MEDIUMBLOB()
        if field.name in {"created_at", "updated_at"} and field.computed is None:
            field.server_default = sa.DefaultClause(sa.text("CURRENT_TIMESTAMP(6)"))
        elif field.default is not None and field.default.is_scalar and field.computed is None:
            value = field.default.arg
            expression = str(int(value)) if isinstance(value, bool) else str(value) if isinstance(value, (int,float)) else "(" + repr(value) + ")" if isinstance(field.type, LONGTEXT) else "'" + str(value).replace("'", "''") + "'"
            field.server_default = sa.DefaultClause(sa.text(expression))
        elif isinstance(field.type, (JSON, UUIDList)) and not field.nullable:
            expression = "(JSON_ARRAY())" if field.default is not None and getattr(field.default.arg, "__name__", "") == "list" else "(JSON_OBJECT())"
            field.server_default = sa.DefaultClause(sa.text(expression))
    for constraint in t.constraints:
        if isinstance(constraint, sa.ForeignKeyConstraint) and not constraint.name:
            constraint.name = ("fk_" + t.name + "_" + "_".join(c.name for c in constraint.columns))[:64]
        elif isinstance(constraint, sa.ForeignKeyConstraint) and constraint.name.startswith("fk_") and t.name not in constraint.name:
            constraint.name = ("fk_" + t.name + "_" + constraint.name[3:])[:64]
        if isinstance(constraint, sa.CheckConstraint):
            expr = str(constraint.sqltext)
            if constraint.name is None:
                import hashlib
                constraint.name = "ck_" + t.name + "_" + hashlib.sha256(expr.encode()).hexdigest()[:8]


def transaction_lock(conn, scope, bucket=0):
    row = conn.execute(sa.select(runtime_locks.c.bucket).where(runtime_locks.c.scope == scope, runtime_locks.c.bucket == bucket).with_for_update()).first()
    if row is None:
        raise RuntimeError("Missing runtime lock rows; run installation verification")


def insert_once(conn, table, values, key):
    # Do not run no-op UPDATE against immutable event history.
    try:
        with conn.begin_nested():
            conn.execute(table.insert().values(**values))
    except sa.exc.IntegrityError as exc:
        if getattr(exc.orig, "args", (None,))[0] != 1062 or not conn.execute(sa.select(table).where(*[table.c[k] == values[k] for k in key])).first():
            raise


def one(conn, t, ident, owner_id=None, lock=False):
    q = sa.select(t).where(t.c.id == ident)
    if owner_id is not None:
        q = q.where(t.c.owner_id == owner_id)
    if lock:
        q = q.with_for_update()
    return conn.execute(q).mappings().first()


def insert(conn, t, **data):
    if t is files:
        from .platform import lock, bound, fingerprint
        lock(conn)
        data.setdefault("index_fingerprint", fingerprint(bound(conn, "embedding")[1]["payload"]))
    if t is document_chunks:
        data.setdefault("index_fingerprint", one(conn, files, data["file_id"])["index_fingerprint"])
    if t is tasks and data.get("kind") != "storage_cleanup":
        if settings().maintenance or (engine.url.database == "study_trail" and (settings().runtime_root / "maintenance").exists()):
            from .core import Problem
            raise Problem("SERVICE_UNAVAILABLE", "平台维护中，暂不受理新任务", 503)
        from .platform import snapshot
        old = data.get("config_snapshot", {})
        frozen = snapshot(conn, old.get("model_id"), generation_required=data["kind"] != "file_process")
        data["config_snapshot"] = {**old, **frozen}
        data["timeout_seconds"] = frozen["limits"]["file_timeout_seconds" if data["kind"] == "file_process" else "ai_timeout_seconds"]
        if data["kind"] == "file_process":
            from .platform import bound
            bound(conn, "embedding")
            conn.execute(files.update().where(files.c.id == data["file_id"]).values(index_fingerprint=frozen["embedding"]["index_fingerprint"]))
    data.setdefault("id", uuid4()) if "id" in t.c else None
    conn.execute(t.insert().values(**data))
    row = one(conn, t, data["id"])
    from .telemetry import inserted
    inserted(conn, t, row)
    return row


def update(conn, t, ident, **data):
    result = conn.execute(t.update().where(t.c.id == ident).values(**data))
    row = one(conn, t, ident) if result.rowcount else None
    if t is tasks and row:
        from .telemetry import task_changed
        task_changed(conn, row)
    return row
