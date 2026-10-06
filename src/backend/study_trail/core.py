import base64
import hashlib
import hmac
import json
import secrets
from contextvars import ContextVar
from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash

from . import db
from .config import settings

password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(16))
TERMINAL = {"succeeded", "failed", "stopped"}
COOKIE = "study_trail_session"
request_identifier = ContextVar("request_identifier", default=None)


class Problem(Exception):
    def __init__(self, code, message, status=422, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def require(row):
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "资源不存在或不可访问", 404)
    return row


def envelope(data, status=200, request_id=None):
    return JSONResponse(
        jsonable_encoder({"data": data, "request_id": request_id or request_identifier.get() or uuid4()}),
        status_code=status,
    )


def authenticate(conn, request: Request):
    raw = request.cookies.get(COOKIE, "")
    token = hashlib.sha256(raw.encode()).digest()
    session = (
        conn.execute(
            sa.select(db.sessions).where(
                db.sessions.c.token_hash == token,
                db.sessions.c.revoked_at.is_(None),
                db.sessions.c.expires_at > db.now(),
            )
        )
        .mappings()
        .first()
    )
    user = db.one(conn, db.users, session["owner_id"]) if session else None
    if not user or session["auth_version"] != user["auth_version"]:
        raise Problem("AUTH_REQUIRED", "请先登录", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), session["csrf_token"].encode()
        ):
            raise Problem("CSRF_REJECTED", "会话校验失败，请刷新后重试", 403)
    return user, session


def check_origin(request):
    if request.headers.get("origin") not in settings().allowed_origins:
        raise Problem("CSRF_REJECTED", "请求来源未获允许", 403)
    if request.url.path in {"/api/v1/auth/login", "/api/v1/auth/register"} and not request.headers.get(
        "content-type", ""
    ).startswith("application/json"):
        raise Problem("CSRF_REJECTED", "请使用 JSON 请求", 403)


def public(row, *names):
    return {k: row[k] for k in names}


def student(row):
    return public(row, "id", "login", "display_name", "theme")


def storage_path(key):
    root = settings().storage_root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise Problem("INTERNAL_ERROR", "存储标识无效", 500)
    return path


def store_bytes(data):
    key = f"{uuid4().hex[:2]}/{uuid4().hex}"
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def model_config(ident=None):
    ident = ident or settings().default_model()
    model = settings().models().get(ident)
    if not model or not model.get("enabled"):
        raise Problem("MODEL_UNAVAILABLE", "当前模型尚不可用，请稍后再试", 503)
    provider = model.get("provider") or settings().model_catalog().providers["generation"].model_dump()
    return {"model_id": ident, **model, "provider": provider}


def agent_config(conn, agent):
    from .mcp_tools import selected

    return {
        **model_config(agent["model_id"]),
        "system_prompt": agent["system_prompt"],
        "config_version": agent["config_version"],
        "mcp_servers": selected(agent["mcp_server_ids"]),
        "knowledge_base_ids": [
            str(x)
            for x in conn.execute(
                sa.select(db.agent_knowledge_bases.c.knowledge_base_id).where(
                    db.agent_knowledge_bases.c.agent_id == agent["id"]
                )
            ).scalars()
        ],
    }


def idem(conn, request, owner, payload, action):
    key = request.headers.get("idempotency-key", "")
    if not 1 <= len(key) <= 128 or any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise Problem("VALIDATION_ERROR", "缺少有效 Idempotency-Key")
    # Serialize only this owner's key, including concurrent first requests.
    lock = int.from_bytes(hashlib.sha256(f"{owner}:{key}".encode()).digest()[:8], signed=True)
    conn.execute(sa.text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
    finger = hashlib.sha256(
        json.dumps(
            [request.method, request.url.path, jsonable_encoder(payload)], sort_keys=True, ensure_ascii=False
        ).encode()
    ).digest()
    old = (
        conn.execute(
            sa.select(db.idempotency_records).where(
                db.idempotency_records.c.owner_id == owner, db.idempotency_records.c.key == key
            )
        )
        .mappings()
        .first()
    )
    if old:
        if old["request_fingerprint"] != finger:
            raise Problem("IDEMPOTENCY_CONFLICT", "此操作标识已用于其他请求", 409)
        for kind, ident in old["resource_refs"]:
            if db.one(conn, db.metadata.tables[kind], UUID(ident), owner) is None:
                raise Problem("RESOURCE_GONE", "原操作资源已经删除", 410)
        return envelope(old["response_snapshot"], old["response_status"])
    body, status, refs = action()
    safe = jsonable_encoder(body)
    db.insert(
        conn,
        db.idempotency_records,
        owner_id=owner,
        key=key,
        method=request.method,
        path=request.url.path,
        request_fingerprint=finger,
        response_status=status,
        response_snapshot=safe,
        resource_refs=refs,
    )
    return envelope(safe, status)


def paginate(conn, t, owner, scope, clauses=(), cursor=None, limit=20, order=None):
    if not 1 <= limit <= 100:
        raise Problem("INVALID_CURSOR", "分页数量应为 1–100")
    if order is None:
        order = t.c.created_at
    direction = "asc" if t is db.messages or t is db.document_chunks else "desc"
    signature = [str(owner), scope, direction]
    q = sa.select(t).where(t.c.owner_id == owner, *clauses)
    if cursor:
        try:
            encoded, mac = cursor.split(".")
            secret = settings().cursor_secret
            expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
            if not secret or not hmac.compare_digest(mac, expected):
                raise ValueError()
            obj = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if obj["scope"] != signature:
                raise ValueError()
            edge = obj["edge"]
            if direction == "desc":
                from datetime import datetime

                edge = datetime.fromisoformat(edge)
            comparator = sa.tuple_(order, t.c.id)
            boundary = sa.tuple_(edge, UUID(obj["id"]))
            q = q.where(comparator < boundary if direction == "desc" else comparator > boundary)
        except (ValueError, KeyError, TypeError):
            raise Problem("INVALID_CURSOR", "分页标识无效或已失效")
    q = q.order_by(
        order.asc() if direction == "asc" else order.desc(),
        t.c.id.asc() if direction == "asc" else t.c.id.desc(),
    ).limit(limit + 1)
    rows = list(conn.execute(q).mappings())
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        edge = (
            last["sequence_no"]
            if t is db.messages
            else last["chunk_index"]
            if t is db.document_chunks
            else last.get("last_accessed_at") or last["created_at"]
            if t is db.conversations
            else last["created_at"]
        )
        obj = jsonable_encoder({"scope": signature, "edge": edge, "id": last["id"]})
        encoded = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
        next_cursor = (
            encoded
            + "."
            + hmac.new(settings().cursor_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        )
    return rows[:limit], next_cursor


def attachment(row):
    return {
        **public(row, "id", "state", "original_name", "media_type", "byte_size", "width", "height"),
        "content_url": f"/api/v1/attachments/{row['id']}/content",
    }


def source(row):
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title_snapshot"],
        "file_id": row["file_id"],
        "status": "external" if row["kind"] == "web" else "available" if row["file_id"] else "deleted",
        "url": row["url_snapshot"],
        "locator": row["locator_snapshot"],
        "excerpt": row["excerpt_snapshot"],
        "captured_at": row["captured_at"],
    }


def linked_sources(conn, link, key, ident):
    q = (
        sa.select(db.sources)
        .join(link, db.sources.c.id == link.c.source_id)
        .where(link.c[key] == ident)
        .order_by(link.c.position)
    )
    return [source(r) for r in conn.execute(q).mappings()]


def exercise(row):
    return public(
        row, "id", "message_id", "conversation_id", "position", "question_text", "provenance", "source_ids"
    )


def message(conn, row):
    out = public(
        row,
        "id",
        "conversation_id",
        "sequence_no",
        "role",
        "origin",
        "content_text",
        "skill",
        "response_status",
        "user_message_id",
        "task_id",
        "created_at",
    )
    out["attachments"] = [
        attachment(r)
        for r in conn.execute(
            sa.select(db.attachments)
            .join(db.message_attachments, db.attachments.c.id == db.message_attachments.c.attachment_id)
            .where(db.message_attachments.c.message_id == row["id"])
            .order_by(db.message_attachments.c.position)
        ).mappings()
    ]
    out["sources"] = linked_sources(conn, db.message_sources, "message_id", row["id"])
    out["exercises"] = [
        exercise(r)
        for r in conn.execute(
            sa.select(db.exercises)
            .where(db.exercises.c.message_id == row["id"])
            .order_by(db.exercises.c.position)
        ).mappings()
    ]
    return out


def plan(conn, row):
    return {
        **public(
            row,
            "id",
            "agent_id",
            "name",
            "content_version",
            "source_conversation_id",
            "source_message_id",
            "created_at",
            "updated_at",
        ),
        "content": {"title": row["name"], **row["content"]},
        "sources": linked_sources(conn, db.plan_sources, "plan_id", row["id"]),
    }


def agent(conn, row):
    out = public(
        row,
        "id",
        "name",
        "description",
        "system_prompt",
        "model_id",
        "config_version",
        "mcp_server_ids",
        "skill_ids",
        "created_at",
        "updated_at",
    )
    out["knowledge_base_ids"] = list(
        conn.execute(
            sa.select(db.agent_knowledge_bases.c.knowledge_base_id).where(
                db.agent_knowledge_bases.c.agent_id == row["id"]
            )
        ).scalars()
    )
    out["latest_conversation_id"] = conn.execute(
        sa.select(db.conversations.c.id)
        .where(db.conversations.c.agent_id == row["id"])
        .order_by(
            sa.func.coalesce(db.conversations.c.last_accessed_at, db.conversations.c.created_at).desc(),
            db.conversations.c.id.desc(),
        )
        .limit(1)
    ).scalar()
    return out


def knowledge_base(conn, row):
    return {
        **public(row, "id", "name", "created_at", "updated_at"),
        "file_count": conn.execute(
            sa.select(sa.func.count()).select_from(db.files).where(db.files.c.knowledge_base_id == row["id"])
        ).scalar(),
        "ready_file_count": conn.execute(
            sa.select(sa.func.count())
            .select_from(db.files)
            .where(db.files.c.knowledge_base_id == row["id"], db.files.c.status == "ready")
        ).scalar(),
    }


def file(conn, row):
    out = public(
        row,
        "id",
        "knowledge_base_id",
        "original_name",
        "extension",
        "byte_size",
        "status",
        "parse_version",
        "indexed_at",
        "created_at",
        "updated_at",
    )
    out["error"] = {"code": row["error_code"], "message": row["error_message"]} if row["error_code"] else None
    out["latest_task_id"] = conn.execute(
        sa.select(db.tasks.c.id)
        .where(db.tasks.c.file_id == row["id"])
        .order_by(db.tasks.c.created_at.desc())
        .limit(1)
    ).scalar()
    return out


PHASES = {
    "queued": "等待处理",
    "context": "准备上下文",
    "retrieve": "检索资料",
    "rerank": "整理资料",
    "search": "网络补充",
    "generate": "生成回答",
    "validate": "整理计划",
    "save": "保存结果",
    "parse": "提取文字",
    "embed": "建立索引",
    "cleanup": "清理文件",
}


def task(conn, row):
    out = public(
        row,
        "id",
        "kind",
        "action",
        "status",
        "attempt_no",
        "user_message_id",
        "assistant_message_id",
        "revision",
        "cancel_requested",
        "timeout_seconds",
        "deadline_at",
        "started_at",
        "finished_at",
    )
    out["progress"] = {"phase": row["phase"], "label": PHASES.get(row["phase"], row["phase"])}
    out["result"] = {
        "preview_text": row["preview_text"],
        "sources": [],
        "exercise_ids": [],
        "plan_mutation": None,
        **row["result_snapshot"],
    }
    for s in out["result"]["sources"]:
        if s.get("kind") == "file" and (
            not s.get("file_id") or not db.one(conn, db.files, UUID(str(s["file_id"])), row["owner_id"])
        ):
            s["status"] = "deleted"
    out["error"] = {"code": row["error_code"], "message": row["error_message"]} if row["error_code"] else None
    out["request_input"] = None
    if row["kind"] == "chat":
        inp = dict(row["input_snapshot"])
        target = inp.get("target_plan")
        if target:
            target = {
                **target,
                "status": "available"
                if db.one(conn, db.learning_plans, UUID(target["id"]), row["owner_id"])
                else "deleted",
            }
        out["request_input"] = {
            k: inp.get(k, [] if k == "attachment_ids" else None)
            for k in ["content_text", "attachment_ids", "answer_exercise_id", "skill_id"]
        }
        out["request_input"]["target_plan"] = target
    return out


def cleanup(conn, owner, key, attempt=1, previous=None):
    return db.insert(
        conn,
        db.tasks,
        owner_id=owner,
        kind="storage_cleanup",
        phase="cleanup",
        timeout_seconds=600,
        attempt_no=attempt,
        previous_task_id=previous,
        input_snapshot={"storage_key": key},
        next_attempt_at=db.now() + timedelta(seconds=min(3600, 60 * 2 ** min(attempt - 2, 6)))
        if attempt > 1
        else db.now(),
    )
