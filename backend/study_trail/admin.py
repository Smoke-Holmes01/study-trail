"""Administrator API. All operational DTOs are explicit allow-list projections."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictBool

from . import core as c
from . import db
from . import platform as p
from . import schemas as s
from .config import settings

router = APIRouter(prefix="/api/admin", tags=["management"])
COOKIE = "study_trail_admin_session"


def audit(conn, actor, action, kind="system", target=None, result="success", code=None):
    try:
        conn.execute(
            db.admin_operation_logs.insert().values(
                actor_id=actor,
                action=action,
                target_kind=kind,
                target_id=target,
                result=result,
                safe_error_code=code,
                request_id=c.request_identifier.get() or uuid4(),
            )
        )
    except sa.exc.SQLAlchemyError:
        raise c.Problem("AUDIT_UNAVAILABLE", "操作日志暂时不可用，本次修改未保存", 503) from None


def context(request: Request):
    actor = None
    try:
        with db.engine.begin() as conn:
            user, session = c.authenticate(conn, request, realm="admin")
            actor = user["id"]
            yield conn, user, session
    except Exception as exc:
        if actor and request.method not in {"GET", "HEAD"}:
            with db.engine.begin() as conn:
                audit(
                    conn,
                    actor,
                    request.method + " " + request.url.path,
                    result="failure",
                    code=getattr(exc, "code", "INTERNAL_ERROR"),
                )
        raise


D = Depends(context, scope="function")


def user_dto(row):
    return {
        **c.public(row, "id", "login", "display_name", "enabled", "created_at", "last_login_at"),
        "lock_version": row["auth_version"],
    }


class Status(s.Input):
    enabled: StrictBool
    expected_version: int = Field(gt=0, strict=True)
    replace_active_id: UUID | None = None
    confirm_replace: bool = False


class Binding(s.Input):
    config_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    confirm_switch: bool = False


class ConfigDelete(s.Input):
    expected_version: int = Field(gt=0, strict=True)


def version(row, expected):
    if type(expected) is not int or expected != row["lock_version"]:
        raise c.Problem("CONFIG_VERSION_CONFLICT", "记录已被修改，请重新载入后保存", 409)


def config(conn, kind, ident):
    if kind not in {"model", "prompt", "skill", "mcp"}:
        raise c.Problem("RESOURCE_NOT_FOUND", "配置类别不存在", 404)
    row = c.require(db.one(conn, db.platform_configs, ident))
    if row["kind"] != kind or row["deleted_at"] is not None:
        raise c.Problem("RESOURCE_NOT_FOUND", "配置不存在", 404)
    return row


def write(conn, user, request, payload, action, target_kind="system", target_id=None):
    # Only an HMAC of secret values enters the persisted idempotency fingerprint.
    safe = json.loads(json.dumps(payload, default=str))

    def scrub(value):
        if isinstance(value, dict):
            return {
                k: (
                    {
                        slot: hmac.new(
                            settings().cursor_secret.encode(), str(secret).encode(), hashlib.sha256
                        ).hexdigest()
                        for slot, secret in v.items()
                    }
                    if k == "secret_updates" and isinstance(v, dict)
                    else scrub(v)
                )
                for k, v in value.items()
            }
        return [scrub(v) for v in value] if isinstance(value, list) else value

    def run():
        result, status = action()
        audit(conn, user["id"], request.method + " " + request.url.path, target_kind, target_id)
        return result, status, []

    return c.idem(conn, request, user["id"], scrub(safe), run)


@router.post("/auth/login", operation_id="AD-A01")
def login(body: s.Login, request: Request):
    c.check_origin(request)
    with db.engine.begin() as conn:
        user = (
            conn.execute(sa.select(db.users).where(db.users.c.login_normalized == body.login.lower()))
            .mappings()
            .first()
        )
        valid = c.password_hash.verify(body.password, user["password_hash"] if user else c.DUMMY_HASH)
        if not valid or not user or user["role"] != "admin":
            raise c.Problem("INVALID_CREDENTIALS", "管理员账号或密码错误", 401)
        if not user["enabled"]:
            raise c.Problem("ACCOUNT_DISABLED", "账户已停用", 403)
        raw = secrets.token_urlsafe(32)
        session = db.insert(
            conn,
            db.sessions,
            owner_id=user["id"],
            realm="admin",
            token_hash=hashlib.sha256(raw.encode()).digest(),
            csrf_token=secrets.token_hex(32),
            auth_version=user["auth_version"],
            expires_at=db.now() + timedelta(days=7),
        )
        db.update(conn, db.users, user["id"], last_login_at=db.now())
        audit(conn, user["id"], "admin_login")
        response = c.envelope(
            {
                "admin": c.public(user, "id", "login", "display_name", "role"),
                "csrf_token": session["csrf_token"],
                "expires_at": session["expires_at"],
            }
        )
        response.set_cookie(
            COOKIE,
            raw,
            httponly=True,
            samesite="lax",
            secure=settings().secure_cookies,
            path="/api/admin",
            max_age=604800,
        )
        return response


@router.get("/me", operation_id="AD-A02")
def me(ctx=D):
    return c.envelope(c.public(ctx[1], "id", "login", "display_name", "role"))


@router.get("/auth/csrf", operation_id="AD-A03")
def csrf(ctx=D):
    return c.envelope({"csrf_token": ctx[2]["csrf_token"]})


@router.post("/auth/logout", operation_id="AD-A04")
def logout(ctx=D):
    db.update(ctx[0], db.sessions, ctx[2]["id"], revoked_at=db.now())
    audit(ctx[0], ctx[1]["id"], "admin_logout")
    response = c.envelope({"logged_out": True})
    response.delete_cookie(COOKIE, path="/api/admin")
    return response


def paginate(conn, table, request, actor, clauses, fields=None, order=None):
    limit = request.query_params.get("limit", "20")
    try:
        limit = int(limit)
        if not 1 <= limit <= 100:
            raise ValueError()
    except ValueError:
        raise c.Problem("INVALID_CURSOR", "分页数量应为1—100", 422) from None
    order = order if order is not None else table.c.created_at
    key = table.c.task_id if table is db.task_run_facts else table.c.id
    scope = p.digest(
        [
            str(actor),
            request.url.path,
            sorted((k, v) for k, v in request.query_params.multi_items() if k not in {"cursor", "limit"}),
        ]
    )
    query = sa.select(*(fields or [table])).where(*clauses)
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            encoded, signature = cursor.split(".")
            expected = hmac.new(
                settings().cursor_secret.encode(), encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            obj = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if obj["scope"] != scope:
                raise ValueError()
            query = query.where(
                sa.tuple_(order, key) < sa.tuple_(datetime.fromisoformat(obj["edge"]), UUID(obj["id"]))
            )
        except (ValueError, KeyError, TypeError):
            raise c.Problem("INVALID_CURSOR", "分页标识无效", 422) from None
    rows = [
        dict(r) for r in conn.execute(query.order_by(order.desc(), key.desc()).limit(limit + 1)).mappings()
    ]
    next_cursor = None
    if len(rows) > limit:
        edge = rows[limit - 1]
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"scope": scope, "edge": edge[order.name].isoformat(), "id": str(edge[key.name])}
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        next_cursor = (
            encoded
            + "."
            + hmac.new(settings().cursor_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        )
    return {"items": rows[:limit], "next_cursor": next_cursor}


def query_params(request, allowed):
    if set(request.query_params) - set(allowed) - {"cursor", "limit"}:
        p.invalid("包含未知筛选字段")


@router.get("/users", operation_id="AD-U01")
def users(request: Request, q: str = Query("", max_length=100), enabled: bool | None = None, ctx=D):
    query_params(request, {"q", "enabled"})
    clauses = [db.users.c.role == "user"]
    if q:
        clauses.append(
            sa.or_(db.users.c.login.ilike("%" + q + "%"), db.users.c.display_name.ilike("%" + q + "%"))
        )
    if enabled is not None:
        clauses.append(db.users.c.enabled == enabled)
    page = paginate(ctx[0], db.users, request, ctx[1]["id"], clauses)
    page["items"] = [user_dto(r) for r in page["items"]]
    return c.envelope(page)


@router.patch("/users/{ident}/status", operation_id="AD-U02")
def user_status(ident: UUID, body: Status, request: Request, ctx=D):
    conn, actor, _ = ctx

    def action():
        row = c.require(db.one(conn, db.users, ident, lock=True))
        if row["role"] != "user":
            raise c.Problem("RESOURCE_NOT_FOUND", "用户不存在", 404)
        version({"lock_version": row["auth_version"]}, body.expected_version)
        row = db.update(conn, db.users, ident, enabled=body.enabled, auth_version=row["auth_version"] + 1)
        conn.execute(db.sessions.update().where(db.sessions.c.owner_id == ident).values(revoked_at=db.now()))
        return user_dto(row), 200

    return write(conn, actor, request, body.model_dump(), action, "user", ident)


@router.get("/configs/{kind}", operation_id="AD-C01")
def configs(
    kind: str,
    request: Request,
    q: str = Query("", max_length=100),
    enabled: bool | None = None,
    purpose: str | None = None,
    model_kind: str | None = None,
    ctx=D,
):
    query_params(request, {"q", "enabled", "purpose", "model_kind"})
    if kind not in {"model", "prompt", "skill", "mcp"}:
        raise c.Problem("RESOURCE_NOT_FOUND", "配置类别不存在", 404)
    clauses = [db.platform_configs.c.kind == kind, db.platform_configs.c.deleted_at.is_(None)]
    if q:
        clauses.append(db.platform_configs.c.name.ilike("%" + q + "%"))
    if enabled is not None:
        clauses.append(db.platform_configs.c.enabled == enabled)
    if purpose:
        if purpose not in p.PURPOSES:
            p.invalid("未知用途")
        clauses.append(db.platform_configs.c.purpose == purpose)
    if model_kind:
        if model_kind not in {"generative", "embedding", "rerank"}:
            p.invalid("未知模型类别")
        clauses.append(
            db.platform_configs.c.current_revision_id.in_(
                sa.select(db.config_revisions.c.id).where(
                    db.config_revisions.c.payload["model_kind"].astext == model_kind
                )
            )
        )
    page = paginate(ctx[0], db.platform_configs, request, ctx[1]["id"], clauses)
    page["items"] = [p.projection(ctx[0], r) for r in page["items"]]
    return c.envelope(page)


@router.post("/configs/{kind}", operation_id="AD-C02")
def create(kind: str, body: dict, request: Request, ctx=D):
    if kind not in {"model", "prompt", "skill", "mcp"}:
        raise c.Problem("RESOURCE_NOT_FOUND", "配置类别不存在", 404)

    def action():
        p.lock(ctx[0])
        row = p.create(ctx[0], kind, body, ctx[1]["id"])
        return p.projection(ctx[0], row), 201

    return write(ctx[0], ctx[1], request, body, action, kind)


@router.get("/configs/{kind}/{ident}", operation_id="AD-C03")
def detail(kind: str, ident: UUID, ctx=D):
    return c.envelope(p.projection(ctx[0], config(ctx[0], kind, ident)))


@router.patch("/configs/{kind}/{ident}", operation_id="AD-C04")
def edit(kind: str, ident: UUID, body: dict, request: Request, ctx=D):
    def action():
        p.lock(ctx[0])
        row = config(ctx[0], kind, ident)
        version(row, body.get("expected_version"))
        if set(body) - {"name", "description", "payload", "secret_updates", "expected_version"}:
            p.invalid("包含不可修改字段")
        name, description = body.get("name", row["name"]), body.get("description", row["description"])
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 80
            or not isinstance(description, str)
            or len(description) > 500
        ):
            p.invalid("名称或说明不合法")
        old = db.one(ctx[0], db.config_revisions, row["current_revision_id"])
        new_payload = p.validate(kind, body.get("payload", old["payload"]), row["purpose"], row["stable_id"])
        if kind == "model" and new_payload.get("model_kind") != old["payload"]["model_kind"]:
            p.invalid("已有模型不能更改用途")
        p.revision(ctx[0], row, new_payload, body.get("secret_updates"), ctx[1]["id"])
        row = db.update(ctx[0], db.platform_configs, ident, name=name.strip(), description=description)
        return p.projection(ctx[0], row), 200

    return write(ctx[0], ctx[1], request, body, action, kind, ident)


def switch_prompt(conn, row, replacement, confirmed):
    old = (
        conn.execute(
            sa.select(db.platform_configs).where(
                db.platform_configs.c.kind == "prompt",
                db.platform_configs.c.purpose == row["purpose"],
                db.platform_configs.c.enabled.is_(True),
                db.platform_configs.c.id != row["id"],
            )
        )
        .mappings()
        .first()
    )
    if old:
        if not confirmed or replacement != old["id"]:
            raise c.Problem("CONFIRM_REPLACEMENT_REQUIRED", "请确认替换同用途的当前提示词", 409)
        db.update(conn, db.platform_configs, old["id"], enabled=False, lock_version=old["lock_version"] + 1)
    updated = db.update(
        conn, db.platform_configs, row["id"], enabled=True, lock_version=row["lock_version"] + 1
    )
    p.bind(conn, "prompt:" + row["purpose"], updated)
    return updated


@router.patch("/configs/{kind}/{ident}/status", operation_id="AD-C05")
def config_status(kind: str, ident: UUID, body: Status, request: Request, ctx=D):
    def action():
        p.lock(ctx[0])
        row = config(ctx[0], kind, ident)
        version(row, body.expected_version)
        bound = (
            ctx[0]
            .execute(sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.config_id == ident))
            .scalars()
            .all()
        )
        if not body.enabled and bound:
            raise c.Problem("REQUIRED_CONFIG_MISSING", "当前配置仍被必要用途使用，请先切换替代配置", 409)
        if kind == "prompt" and body.enabled:
            row = switch_prompt(ctx[0], row, body.replace_active_id, body.confirm_replace)
        else:
            row = db.update(
                ctx[0], db.platform_configs, ident, enabled=body.enabled, lock_version=row["lock_version"] + 1
            )
        return p.projection(ctx[0], row), 200

    return write(ctx[0], ctx[1], request, body.model_dump(), action, kind, ident)


@router.delete("/configs/{kind}/{ident}", operation_id="AD-C07")
def delete_config(kind: str, ident: UUID, body: ConfigDelete, request: Request, ctx=D):
    def action():
        p.lock(ctx[0])
        row = config(ctx[0], kind, ident)
        version(row, body.expected_version)
        if ctx[0].scalar(sa.select(db.platform_bindings.c.slot).where(db.platform_bindings.c.config_id == ident).limit(1)):
            raise c.Problem("CONFIG_IN_USE", "此配置仍用于生效用途，请先切换替代配置后删除。", 409)
        used = {
            "model": db.agents.c.model_id == row["stable_id"],
            "mcp": db.agents.c.mcp_server_ids.contains([row["stable_id"]]),
            "skill": db.agents.c.skill_ids.contains([row["stable_id"]]),
        }.get(kind)
        if used is not None and ctx[0].scalar(sa.select(db.agents.c.id).where(used).limit(1)):
            raise c.Problem("CONFIG_IN_USE", "此配置仍被智能体选择，请先解除关联或更换配置后删除。", 409)
        db.update(ctx[0], db.platform_configs, ident, enabled=False, deleted_at=db.now(), lock_version=row["lock_version"] + 1)
        return {"deleted_id": ident}, 200

    return write(ctx[0], ctx[1], request, body.model_dump(), action, kind, ident)


@router.get("/bindings", operation_id="AD-B01")
def bindings(ctx=D):
    return c.envelope(
        {
            "items": [
                dict(r)
                for r in ctx[0]
                .execute(sa.select(db.platform_bindings).order_by(db.platform_bindings.c.slot))
                .mappings()
            ]
        }
    )


@router.patch("/bindings/{slot}", operation_id="AD-B02")
def binding(slot: str, body: Binding, request: Request, ctx=D):
    def action():
        p.lock(ctx[0])
        row = c.require(db.one(ctx[0], db.platform_configs, body.config_id))
        if row["deleted_at"] is not None:
            raise c.Problem("RESOURCE_NOT_FOUND", "配置不存在", 404)
        if not body.confirm_switch:
            raise c.Problem("CONFIRM_REPLACEMENT_REQUIRED", "请确认切换生效配置", 409)
        if slot.startswith("prompt:"):
            current = (
                ctx[0]
                .execute(sa.select(db.platform_bindings).where(db.platform_bindings.c.slot == slot))
                .mappings()
                .first()
            )
            if not current or current["lock_version"] != body.expected_version:
                raise c.Problem("CONFIG_VERSION_CONFLICT", "配置已更新", 409)
            if slot != "prompt:" + str(row["purpose"]):
                p.invalid("提示词用途不一致")
            switch_prompt(ctx[0], row, current["config_id"], True)
        else:
            p.bind(ctx[0], slot, row, expected=body.expected_version)
        return {"slot": slot, "config_id": body.config_id}, 200

    return write(ctx[0], ctx[1], request, body.model_dump(), action, "binding", body.config_id)


@router.get("/settings", operation_id="AD-S01")
def get_settings(ctx=D):
    config, revision = p.bound(ctx[0], "limits")
    return c.envelope({**revision["payload"], "lock_version": config["lock_version"], "bounds": p.BOUNDS})


@router.patch("/settings", operation_id="AD-S02")
def settings_edit(body: dict, request: Request, ctx=D):
    def action():
        p.lock(ctx[0])
        config, rev = p.bound(ctx[0], "limits")
        version(config, body.get("expected_version"))
        if set(body) - set(p.DEFAULT_LIMITS) - {"expected_version"}:
            p.invalid("包含未知限制")
        payload = {**rev["payload"], **{k: v for k, v in body.items() if k != "expected_version"}}
        new = p.revision(ctx[0], config, payload, None, ctx[1]["id"])
        return {**new["payload"], "lock_version": config["lock_version"] + 1, "bounds": p.BOUNDS}, 200

    return write(ctx[0], ctx[1], request, body, action)


@router.post("/config-tests", operation_id="AD-C06")
async def config_test(body: dict, request: Request, ctx=D):
    import asyncio
    import time

    from . import mcp_tools
    from .providers import Gateway

    if set(body) - {"kind", "config_id", "draft", "expected_version"} or body.get("kind") not in {
        "model",
        "mcp",
    }:
        p.invalid("连接测试参数不合法")
    kind = body["kind"]
    ident = body.get("config_id")
    try:
        ident = UUID(ident) if ident else None
    except (ValueError, TypeError):
        p.invalid("配置标识不合法")
    row = config(ctx[0], kind, ident) if ident else None
    if row and "expected_version" in body:
        version(row, body["expected_version"])
    rev = db.one(ctx[0], db.config_revisions, row["current_revision_id"]) if row else None
    draft = body.get("draft") or {}
    if not isinstance(draft, dict) or set(draft) - {
        "payload",
        "secret_updates",
        "name",
        "description",
        "purpose",
        "stable_id",
    }:
        p.invalid("测试草稿包含未知字段")
    payload = p.validate(kind, draft.get("payload", rev["payload"] if rev else {}))
    test_row = row or {
        "id": uuid4(),
        "kind": kind,
        "stable_id": "connection-test",
        "enabled": True,
        "name": "连接测试",
        "description": "",
    }
    test_rev = rev or {"id": uuid4(), "revision_no": 1, "credential_version_ids": {}}
    runtime = p.runtime(test_row, {**test_rev, "payload": payload})
    updates = draft.get("secret_updates") or {}
    if not isinstance(updates, dict) or any(not isinstance(v, (str, type(None))) for v in updates.values()):
        p.invalid("凭据更新不合法")
    if kind == "model":
        if set(updates) - {"api_key"}:
            p.invalid("未知模型凭据槽")
        if (updates.get("api_key") or "").strip():
            runtime["provider"]["api_key"] = updates["api_key"]
        if not runtime["provider"]["api_key"]:
            p.invalid("请提供连接测试凭据")
    else:
        for slot, secret in updates.items():
            if secret and secret.strip():
                if slot.startswith("env."):
                    runtime["env"][slot[4:]] = secret
                elif slot.startswith("header."):
                    runtime["headers"][slot[7:]] = secret
                else:
                    p.invalid("MCP凭据槽须以env.或header.开头")
    # The advisory idempotency lock remains held through the bounded probe.
    reservation = write(ctx[0], ctx[1], request, body, lambda: ({"pending": True}, 202), kind, ident)
    if not json.loads(reservation.body)["data"].get("pending"):
        return reservation
    start = time.monotonic()
    outcome, code, tools = "success", None, None
    task = {
        "owner_id": ctx[1]["id"],
        "kind": "chat",
        "purpose": "connection_test",
        "deadline_at": db.now() + timedelta(seconds=30),
        "config_snapshot": runtime,
    }
    try:
        async with asyncio.timeout(30):
            if kind == "mcp":
                async with mcp_tools.connect(runtime, 30) as session:
                    tools = await mcp_tools.discover(session, test_row["stable_id"], runtime, 30)
                tools = [{"name": t["name"]} for t in tools]
            else:
                gateway = Gateway(task)
                model_kind = payload["model_kind"]
                if model_kind == "generative":
                    task["config_snapshot"] = {
                        **runtime,
                        "max_output_tokens": min(runtime["max_output_tokens"], 32),
                    }
                    await gateway.complete([{"role": "user", "content": "只回答OK"}])
                elif model_kind == "embedding":
                    task["config_snapshot"] = {"embedding": runtime}
                    await gateway.embed(["连接测试"])
                else:
                    task["config_snapshot"] = {"rerank": runtime}
                    await gateway.rerank("学习", ["自主学习", "天气"])
    except TimeoutError:
        outcome, code = "timeout", "CONNECTION_TIMEOUT"
    except Exception as exc:
        outcome, code = "failure", getattr(exc, "code", "CONNECTION_FAILED")
        if "TIMEOUT" in code:
            outcome = "timeout"
    result = {
        "outcome": outcome,
        "error_code": code,
        "message": {
            "success": "连接检查通过",
            "failure": "连接检查失败，请核对地址、凭据和服务状态",
            "timeout": "连接检查超时",
        }[outcome],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
        "discovered_tools": tools,
    }
    from fastapi.encoders import jsonable_encoder

    ctx[0].execute(
        db.idempotency_records.update()
        .where(
            db.idempotency_records.c.owner_id == ctx[1]["id"],
            db.idempotency_records.c.key == request.headers["idempotency-key"],
        )
        .values(response_snapshot=jsonable_encoder(result), response_status=200)
    )
    audit(
        ctx[0],
        ctx[1]["id"],
        "connection_test_result",
        kind,
        ident,
        "success" if outcome == "success" else "failure",
        code,
    )
    return c.envelope(result)


def times(request):
    try:
        start = datetime.fromisoformat(
            request.query_params.get("from", (db.now() - timedelta(days=30)).isoformat()).replace(
                "Z", "+00:00"
            )
        )
        end = datetime.fromisoformat(
            request.query_params.get("to", db.now().isoformat()).replace("Z", "+00:00")
        )
        if not start.tzinfo or not end.tzinfo or start >= end:
            raise ValueError()
        return start.astimezone(UTC), end.astimezone(UTC)
    except ValueError:
        p.invalid("时间范围须为带时区日期且起点早于终点")


def filters(request, table, mapping):
    query_params(request, set(mapping) | {"from", "to"})
    start, end = times(request)
    order = (
        table.c.accepted_at
        if table is db.task_run_facts
        else table.c.started_at
        if table is db.invocation_attempts
        else table.c.created_at
    )
    clauses = [order >= start, order < end]
    for name, column in mapping.items():
        if name in request.query_params:
            value = request.query_params[name]
            if name in {"owner_id", "actor_id"}:
                try:
                    value = UUID(value)
                except ValueError:
                    p.invalid("账户标识格式不正确")
            elif name == "status" and value not in (
                {"running", "succeeded", "failed", "stopped", "unknown"}
                if table is db.invocation_attempts
                else {"queued", "running", "succeeded", "failed", "stopped"}
            ):
                p.invalid("状态不合法")
            elif name == "category" and value not in {"model", "mcp_tool"}:
                p.invalid("调用类别不合法")
            elif name == "kind" and value not in {"chat", "prompt_generate", "prompt_polish", "file_process"}:
                p.invalid("任务类别不合法")
            elif name == "purpose" and value not in {
                "learning",
                "file_process",
                "prompt_assist",
                "connection_test",
            }:
                p.invalid("调用用途不合法")
            elif name == "action" and value not in {"plan_create", "plan_update", "exercise", "answer", "tutor", "feedback", "clarify", "unknown"}:
                p.invalid("任务动作不合法")
            clauses.append(column == value)
    return clauses, order


@router.get("/agents", operation_id="AD-O01")
def agents(request: Request, ctx=D):
    clauses, order = filters(request, db.agents, {"owner_id": db.agents.c.owner_id})
    fields = [db.agents.c.id, db.agents.c.owner_id, db.agents.c.created_at]
    page = paginate(ctx[0], db.agents, request, ctx[1]["id"], clauses, fields, order)
    for row in page["items"]:
        row["agent_id"] = row.pop("id")
        for name, table, column in [
            ("conversation_count", db.conversations, db.conversations.c.agent_id),
            ("plan_count", db.learning_plans, db.learning_plans.c.agent_id),
            ("knowledge_base_count", db.agent_knowledge_bases, db.agent_knowledge_bases.c.agent_id),
        ]:
            row[name] = ctx[0].scalar(
                sa.select(sa.func.count()).select_from(table).where(column == row["agent_id"])
            )
        row["last_task_at"] = ctx[0].scalar(
            sa.select(sa.func.max(db.task_run_facts.c.accepted_at)).where(
                db.task_run_facts.c.source_agent_id == row["agent_id"]
            )
        )
    return c.envelope(page)


def run_dto(conn, row, file=False):
    result = {
        k: row[k]
        for k in (
            "task_id",
            "owner_id",
            "kind",
            "action",
            "status",
            "accepted_at",
            "started_at",
            "finished_at",
            "safe_error_code",
            "model_config_id",
        )
    }
    result["duration_ms"] = (
        int((row["finished_at"] - row["started_at"]).total_seconds() * 1000)
        if row["finished_at"] and row["started_at"]
        else None
    )
    result["resource_deleted"] = db.one(conn, db.tasks, row["task_id"]) is None
    for category, name in [("model", "model_calls"), ("mcp_tool", "tool_calls")]:
        result[name] = conn.scalar(
            sa.select(sa.func.count())
            .select_from(db.invocation_attempts)
            .where(
                db.invocation_attempts.c.task_id == row["task_id"],
                db.invocation_attempts.c.category == category,
            )
        )
    if file:
        result.update(file_id=row["source_file_id"], extension=row["file_extension"])
    return result


def run_page(request, ctx, file):
    table = db.task_run_facts
    clauses, order = filters(
        request,
        table,
        {
            "owner_id": table.c.owner_id,
            "kind": table.c.kind,
            "action": table.c.action,
            "status": table.c.status,
        },
    )
    clauses.append(table.c.kind == "file_process" if file else table.c.kind != "file_process")
    page = paginate(ctx[0], table, request, ctx[1]["id"], clauses, order=order)
    page["items"] = [run_dto(ctx[0], row, file) for row in page["items"]]
    return c.envelope(page)


@router.get("/file-runs", operation_id="AD-O02")
def file_runs(request: Request, ctx=D):
    return run_page(request, ctx, True)


@router.get("/task-runs", operation_id="AD-O03")
def task_runs(request: Request, ctx=D):
    return run_page(request, ctx, False)


@router.get("/task-runs/{ident}", operation_id="AD-O04")
def task_run(ident: UUID, ctx=D):
    row = c.require(
        ctx[0]
        .execute(sa.select(db.task_run_facts).where(db.task_run_facts.c.task_id == ident))
        .mappings()
        .first()
    )
    return c.envelope(run_dto(ctx[0], row, row["kind"] == "file_process"))


@router.get("/task-runs/{ident}/calls", operation_id="AD-O05")
def calls(ident: UUID, request: Request, ctx=D):
    c.require(
        ctx[0].execute(sa.select(db.task_run_facts).where(db.task_run_facts.c.task_id == ident)).first()
    )
    clauses, order = filters(
        request,
        db.invocation_attempts,
        {
            "category": db.invocation_attempts.c.category,
            "status": db.invocation_attempts.c.status,
            "purpose": db.invocation_attempts.c.purpose,
        },
    )
    clauses.append(db.invocation_attempts.c.task_id == ident)
    # Task detail shows its complete call history unless a time range was explicitly selected.
    if "from" not in request.query_params and "to" not in request.query_params:
        clauses = clauses[2:]
    page = paginate(ctx[0], db.invocation_attempts, request, ctx[1]["id"], clauses, order=order)
    for row in page["items"]:
        row.pop("operation_key")
        row.pop("actor_id")
        row["duration_ms"] = (
            int((row["finished_at"] - row["started_at"]).total_seconds() * 1000)
            if row["finished_at"]
            else None
        )
    return c.envelope(page)


def metric(conn, key, value, start, end=None):
    row = (
        conn.execute(sa.select(db.metric_coverage).where(db.metric_coverage.c.metric_key == key))
        .mappings()
        .first()
    )
    if not row or key == "calls" and end is not None and end <= row["coverage_start"]:
        return {
            "value": None,
            "state": "not_collected",
            "coverage_from": None,
            "reason_code": "NOT_COLLECTED",
        }
    partial = not row["history_complete"] and start < row["coverage_start"]
    return {
        "value": value,
        "state": "partial" if partial else "complete",
        "coverage_from": row["coverage_start"],
        "reason_code": row["note_code"] if partial else None,
    }


@router.get("/reports/usage", operation_id="AD-R01")
def usage(request: Request, ctx=D):
    query_params(request, {"from", "to"})
    start, end = times(request)
    totals, new = {}, {}
    for name in sorted(__import__("study_trail.telemetry", fromlist=["RESOURCE_TABLES"]).RESOURCE_TABLES):
        table = db.metadata.tables[name]
        total_query = sa.select(sa.func.count()).select_from(table)
        if table is db.users:
            total_query = total_query.where(db.users.c.role == "user")
        totals[name] = ctx[0].scalar(total_query)
        value = ctx[0].scalar(
            sa.select(sa.func.count())
            .select_from(db.resource_events)
            .where(
                db.resource_events.c.resource_kind == name,
                db.resource_events.c.occurred_at >= start,
                db.resource_events.c.occurred_at < end,
            )
        )
        new[name] = metric(ctx[0], "resources", value, start, end)
    active = ctx[0].scalar(
        sa.select(sa.func.count(sa.distinct(db.task_run_facts.c.owner_id))).where(
            db.task_run_facts.c.accepted_at >= start,
            db.task_run_facts.c.accepted_at < end,
            db.task_run_facts.c.kind.in_(["chat", "prompt_generate", "prompt_polish"]),
        )
    )
    return c.envelope(
        {
            "interval": {"from": start, "to": end},
            "snapshot_at": db.now(),
            "current_totals": totals,
            "new_counts": new,
            "active_users": metric(ctx[0], "tasks", active, start, end),
        }
    )


def aggregates(request, ctx, calls=False):
    table = db.invocation_attempts if calls else db.task_run_facts
    mapping = {"category": table.c.category, "purpose": table.c.purpose} if calls else {"kind": table.c.kind}
    clauses, order = filters(request, table, mapping)
    clauses.append(table.c.purpose != "connection_test" if calls else table.c.kind != "file_process")
    if calls and request.query_params.get("purpose") == "connection_test":
        clauses.pop()
    counts = dict(
        ctx[0]
        .execute(sa.select(table.c.status, sa.func.count()).where(*clauses).group_by(table.c.status))
        .all()
    )
    for status in (
        ["running", "succeeded", "failed", "stopped", "unknown"]
        if calls
        else ["queued", "running", "succeeded", "failed", "stopped"]
    ):
        counts.setdefault(status, 0)
    rows = (
        ctx[0]
        .execute(
            sa.select(table.c.started_at, table.c.finished_at).where(
                *clauses, table.c.finished_at.is_not(None), table.c.started_at.is_not(None)
            )
        )
        .all()
    )
    durations = [(b - a).total_seconds() * 1000 for a, b in rows]
    start, end = times(request)
    result = {
        "interval": {"from": start, "to": end},
        "counts": counts,
        "total_attempts" if calls else "total_tasks": sum(counts.values()),
        "average_duration_ms": sum(durations) / len(durations) if durations else None,
        "completed_duration_samples": len(durations),
        "coverage": metric(ctx[0], "calls" if calls else "tasks", sum(counts.values()), start, end),
    }
    for name in ["category", "model_kind", "config_id", "tool_name"] if calls else ["kind", "action"]:
        result["by_" + name] = {
            str(key) if key is not None else "unknown": value
            for key, value in ctx[0]
            .execute(sa.select(table.c[name], sa.func.count()).where(*clauses).group_by(table.c[name]))
            .all()
        }
    if not calls:
        waits = (
            ctx[0]
            .execute(
                sa.select(table.c.accepted_at, table.c.started_at).where(
                    *clauses, table.c.started_at.is_not(None)
                )
            )
            .all()
        )
        result["average_queue_ms"] = (
            sum((b - a).total_seconds() * 1000 for a, b in waits) / len(waits) if waits else None
        )
    return c.envelope(result)


@router.get("/reports/tasks", operation_id="AD-R02")
def tasks_report(request: Request, ctx=D):
    return aggregates(request, ctx)


@router.get("/reports/calls", operation_id="AD-R03")
def calls_report(request: Request, ctx=D):
    return aggregates(request, ctx, True)


@router.get("/operation-logs", operation_id="AD-L01")
def logs(request: Request, ctx=D):
    query_params(request, {"actor_id", "action", "result", "from", "to"})
    table = db.admin_operation_logs
    start, end = times(request)
    clauses = [table.c.occurred_at >= start, table.c.occurred_at < end]
    for key in ("actor_id", "action", "result"):
        if key in request.query_params:
            value = request.query_params[key]
            if key == "actor_id":
                try:
                    value = UUID(value)
                except ValueError:
                    p.invalid("操作者标识不合法")
            if key == "result" and value not in {"success", "failure"}:
                p.invalid("结果筛选不合法")
            clauses.append(table.c[key] == value)
    return c.envelope(paginate(ctx[0], table, request, ctx[1]["id"], clauses, order=table.c.occurred_at))
