from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, default=now, onupdate=now),
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
    sa.CheckConstraint("theme IN ('light','dark')"),
    sa.CheckConstraint("login_normalized ~ '^[a-z0-9_]{3,32}$'"),
    owner=False,
)
sessions = table(
    "sessions",
    col("token_hash", sa.LargeBinary, unique=True),
    col("csrf_token", sa.String(64)),
    col("auth_version", sa.Integer),
    col("expires_at", sa.DateTime(timezone=True)),
    col("revoked_at", sa.DateTime(timezone=True), nullable=True),
    col("last_seen_at", sa.DateTime(timezone=True), default=now),
)
agents = table(
    "agents",
    col("name", sa.String(50)),
    col("description", sa.String(500), default=""),
    col("system_prompt"),
    col("model_id", sa.String(200)),
    col("config_version", sa.Integer, default=1),
)
knowledge_bases = table("knowledge_bases", col("name", sa.String(50)))
agent_knowledge_bases = sa.Table(
    "agent_knowledge_bases",
    metadata,
    col("agent_id", UUID(as_uuid=True)),
    col("knowledge_base_id", UUID(as_uuid=True)),
    col("owner_id", UUID(as_uuid=True)),
    col("created_at", sa.DateTime(timezone=True), default=now),
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
    col("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
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
    col("expires_at", sa.DateTime(timezone=True), nullable=True),
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
    col("created_at", sa.DateTime(timezone=True), default=now),
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
    col("text_metadata", JSONB, nullable=True),
    col("embedding_model_id", nullable=True),
    col("indexed_at", sa.DateTime(timezone=True), nullable=True),
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
    col("locator", JSONB),
    col("embedding", Vector(1024)),
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
    col("locator_snapshot", JSONB, nullable=True),
    col("captured_at", sa.DateTime(timezone=True), default=now),
    sa.CheckConstraint("kind IN ('file','web')"),
)
learning_plans = table(
    "learning_plans",
    col("agent_id", UUID(as_uuid=True)),
    col("name", sa.String(50)),
    col("content", JSONB),
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
    col("citation_spans", JSONB, default=list),
    col("created_at", sa.DateTime(timezone=True), default=now),
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
    col("created_at", sa.DateTime(timezone=True), default=now),
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
    col("source_ids", JSONB, default=list),
    col("known_answer_snapshot", JSONB, nullable=True),
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
    col("input_snapshot", JSONB, default=dict),
    col("config_snapshot", JSONB, default=dict),
    col("result_snapshot", JSONB, default=dict),
    col("phase", default="queued"),
    col("preview_text", default=""),
    col("error_code", nullable=True),
    col("error_message", nullable=True),
    col("lease_owner", nullable=True),
    col("claim_token", UUID(as_uuid=True), nullable=True, unique=True),
    col("timeout_seconds", sa.Integer, default=300),
    col("queued_at", sa.DateTime(timezone=True), default=now),
    col("started_at", sa.DateTime(timezone=True), nullable=True),
    col("deadline_at", sa.DateTime(timezone=True), nullable=True),
    col("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    col("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    col("finished_at", sa.DateTime(timezone=True), nullable=True),
    col("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued','running','succeeded','failed','stopped')"),
    sa.CheckConstraint("attempt_no >= 1"),
)
conversation_summaries = table(
    "conversation_summaries",
    col("agent_id", UUID(as_uuid=True)),
    col("conversation_id", UUID(as_uuid=True)),
    col("text"),
    col("through_sequence_no", sa.BigInteger),
    col("covered_user_message_ids", ARRAY(UUID(as_uuid=True)), default=list),
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
    col("response_snapshot", JSONB),
    col("resource_refs", JSONB, default=list),
    col("resource_kind", nullable=True),
    col("resource_id", UUID(as_uuid=True), nullable=True),
    sa.UniqueConstraint("owner_id", "key"),
)

sa.Index(
    "uq_active_chat",
    tasks.c.conversation_id,
    unique=True,
    postgresql_where=sa.and_(tasks.c.kind == "chat", tasks.c.status.in_(["queued", "running"])),
)
sa.Index(
    "uq_message_attempt",
    tasks.c.user_message_id,
    tasks.c.attempt_no,
    unique=True,
    postgresql_where=tasks.c.user_message_id.is_not(None),
)
sa.Index("ix_tasks_queue", tasks.c.status, tasks.c.next_attempt_at, tasks.c.queued_at)
sa.Index("ix_tasks_lease", tasks.c.lease_expires_at)
sa.Index(
    "ix_recent_conversations",
    conversations.c.agent_id,
    sa.func.coalesce(conversations.c.last_accessed_at, conversations.c.created_at).desc(),
    conversations.c.id.desc(),
)
for t in list(metadata.tables.values()):
    if "owner_id" in t.c:
        sa.Index(f"ix_{t.name}_owner", t.c.owner_id)
    for c in t.c:
        if c.foreign_keys and c.name != "owner_id":
            sa.Index(f"ix_{t.name}_{c.name}", c)

engine = sa.create_engine(settings().database_url, pool_pre_ping=True, pool_size=8, max_overflow=8)

# Named database checks supplement API validation, including internal worker writes.
CHECKS = {
    "users": {
        "display_name": "length(btrim(display_name)) BETWEEN 1 AND 32",
        "auth_version": "auth_version > 0",
    },
    "sessions": {"token_hash": "octet_length(token_hash) = 32", "csrf": "csrf_token ~ '^[0-9a-f]{64}$'"},
    "agents": {
        "name": "length(btrim(name)) BETWEEN 1 AND 50",
        "prompt": "length(btrim(system_prompt)) BETWEEN 1 AND 10000",
        "version": "config_version > 0",
    },
    "knowledge_bases": {"name": "length(btrim(name)) BETWEEN 1 AND 50"},
    "conversations": {"name": "length(btrim(name)) BETWEEN 1 AND 50", "sequence": "next_sequence_no > 0"},
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
        "text": "length(btrim(content_text)) > 0",
    },
    "sources": {
        "title": "length(btrim(title_snapshot)) > 0",
        "url": "(kind = 'file' AND url_snapshot IS NULL) OR (kind = 'web' AND url_snapshot ~ '^https?://')",
    },
    "exercises": {
        "position": "position >= 0",
        "question": "length(btrim(question_text)) > 0",
        "source_array": "jsonb_typeof(source_ids) = 'array'",
    },
    "learning_plans": {"name": "length(btrim(name)) BETWEEN 1 AND 50", "version": "content_version > 0"},
    "tasks": {
        "kind": "kind IN ('chat','prompt_generate','prompt_polish','file_process','storage_cleanup')",
        "action": "action IS NULL OR action IN ('plan_create','plan_update','exercise','answer','tutor','feedback','clarify')",
        "revision": "revision > 0 AND timeout_seconds > 0",
    },
    "conversation_summaries": {"text": "length(btrim(text)) BETWEEN 1 AND 1200"},
    "message_sources": {"position": "position >= 0"},
    "plan_sources": {"position": "position >= 0 AND content_version > 0"},
}
for table_name, rules in CHECKS.items():
    for name, expression in rules.items():
        metadata.tables[table_name].append_constraint(
            sa.CheckConstraint(expression, name=f"ck_{table_name}_{name}")
        )

for t in metadata.tables.values():
    for field in t.c:
        expression = None
        if field.name == "id":
            expression = "gen_random_uuid()"
        elif field.name in {"created_at", "updated_at"}:
            expression = "now()"
        elif field.default is not None and field.default.is_scalar:
            value = field.default.arg
            expression = (
                "true"
                if value is True
                else "false"
                if value is False
                else str(value)
                if isinstance(value, (int, float))
                else "'" + str(value).replace("'", "''") + "'"
            )
        elif field.default is not None and isinstance(field.type, JSONB):
            expression = (
                "'[]'::jsonb" if getattr(field.default.arg, "__name__", "") == "list" else "'{}'::jsonb"
            )
        elif field.default is not None and isinstance(field.type, ARRAY):
            expression = "'{}'::uuid[]"
        if expression is not None:
            field.server_default = sa.DefaultClause(sa.text(expression))


def one(conn, t, ident, owner_id=None, lock=False):
    q = sa.select(t).where(t.c.id == ident)
    if owner_id is not None:
        q = q.where(t.c.owner_id == owner_id)
    if lock:
        q = q.with_for_update()
    return conn.execute(q).mappings().first()


def insert(conn, t, **data):
    return conn.execute(t.insert().values(**data).returning(t)).mappings().one()


def update(conn, t, ident, **data):
    return conn.execute(t.update().where(t.c.id == ident).values(**data).returning(t)).mappings().first()
