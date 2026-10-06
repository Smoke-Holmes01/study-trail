import asyncio
import hashlib
import io
import json
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from . import core as c
from . import db, mcp_tools, skills
from . import schemas as s
from .body_limit import BodyLimitMiddleware
from .config import settings
from .openapi import install


@asynccontextmanager
async def lifespan(app):
    from .execution import expire_leases

    async def sweep():
        while True:
            try:
                await asyncio.to_thread(expire_leases)
            except sa.exc.OperationalError:
                pass
            await asyncio.sleep(5)

    maintenance = asyncio.create_task(sweep())
    yield
    maintenance.cancel()
    try:
        await maintenance
    except asyncio.CancelledError:
        pass


app = FastAPI(title="学迹 Study Trail", version="0.2.1", lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware)


@app.middleware("http")
async def request_identity(request, call_next):
    request.state.request_id = str(uuid4())
    c.request_identifier.set(request.state.request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(c.Problem)
async def problem(request, exc):
    return JSONResponse(
        {
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": request.state.request_id,
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation(request, exc):
    fields = [{"field": ".".join(map(str, e["loc"][1:])), "message": e["msg"]} for e in exc.errors()]
    # Never include Pydantic's input snapshot (passwords, prompts, file contents).
    return await problem(
        request, c.Problem("VALIDATION_ERROR", "请检查输入内容", details={"field_errors": fields})
    )


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    code = (
        "REQUEST_TOO_LARGE"
        if exc.status_code == 413
        else "RESOURCE_NOT_FOUND"
        if exc.status_code == 404
        else "VALIDATION_ERROR"
    )
    message = (
        "请求超过容量上限，请减少文件或输入内容。"
        if exc.status_code == 413
        else "资源不存在或不可访问"
        if exc.status_code == 404
        else "请求无法处理，请检查输入。"
    )
    return await problem(request, c.Problem(code, message, exc.status_code))


@app.exception_handler(IntegrityError)
async def integrity(request, exc):
    code = (
        "LOGIN_TAKEN"
        if "login_normalized" in str(exc.orig)
        else "CONVERSATION_BUSY"
        if "uq_active_chat" in str(exc.orig)
        else "VALIDATION_ERROR"
    )
    return await problem(
        request, c.Problem(code, "账号已存在" if code == "LOGIN_TAKEN" else "操作与当前资源状态冲突", 409)
    )


@app.exception_handler(Exception)
async def internal(request, exc):
    # Log only exception class and request correlation, never upstream bodies.
    import logging

    logging.getLogger("study_trail").error(
        "request=%s exception=%s", request.state.request_id, type(exc).__name__
    )
    return await problem(request, c.Problem("INTERNAL_ERROR", "服务暂时异常，请稍后重试", 500))


def context(request: Request):
    with db.engine.begin() as conn:
        user, session = c.authenticate(conn, request)
        yield conn, user, session


D = Depends(context)
PREFIX = "/api/v1"


@app.post(PREFIX + "/auth/register", operation_id="AUTH-01")
def register(body: s.Register, request: Request):
    c.check_origin(request)
    with db.engine.begin() as conn:
        if conn.execute(
            sa.select(db.users.c.id).where(db.users.c.login_normalized == body.login.lower())
        ).first():
            raise c.Problem("LOGIN_TAKEN", "账号已存在", 409)
        user = db.insert(
            conn,
            db.users,
            login=body.login,
            login_normalized=body.login.lower(),
            display_name=body.login,
            password_hash=c.password_hash.hash(body.password),
        )
        return c.envelope(c.student(user), 201)


@app.post(PREFIX + "/auth/login", operation_id="AUTH-02")
def login(body: s.Login, request: Request):
    c.check_origin(request)
    with db.engine.begin() as conn:
        user = (
            conn.execute(sa.select(db.users).where(db.users.c.login_normalized == body.login.strip().lower()))
            .mappings()
            .first()
        )
        good = c.password_hash.verify(body.password, user["password_hash"] if user else c.DUMMY_HASH)
        if not good or not user:
            raise c.Problem("INVALID_CREDENTIALS", "账号或密码错误", 401)
        token = secrets.token_urlsafe(32)
        session = db.insert(
            conn,
            db.sessions,
            owner_id=user["id"],
            token_hash=hashlib.sha256(token.encode()).digest(),
            csrf_token=secrets.token_hex(32),
            auth_version=user["auth_version"],
            expires_at=db.now() + timedelta(days=7),
        )
        response = c.envelope(
            {
                "student": c.student(user),
                "csrf_token": session["csrf_token"],
                "expires_at": session["expires_at"],
            }
        )
        response.set_cookie(
            c.COOKIE,
            token,
            max_age=604800,
            httponly=True,
            samesite="lax",
            secure=settings().secure_cookies,
            path="/",
        )
        return response


@app.get(PREFIX + "/me", operation_id="AUTH-03")
def me(ctx=D):
    return c.envelope(c.student(ctx[1]))


@app.get(PREFIX + "/auth/csrf", operation_id="AUTH-04")
def csrf(ctx=D):
    return c.envelope({"csrf_token": ctx[2]["csrf_token"]})


@app.post(PREFIX + "/auth/logout", operation_id="AUTH-05")
def logout(body: s.Empty, ctx=D):
    conn, _, session = ctx
    db.update(conn, db.sessions, session["id"], revoked_at=db.now())
    response = c.envelope({"logged_out": True})
    response.delete_cookie(c.COOKIE, path="/")
    return response


@app.patch(PREFIX + "/me", operation_id="AUTH-06")
def update_me(body: s.MePatch, ctx=D):
    return c.envelope(
        c.student(db.update(ctx[0], db.users, ctx[1]["id"], **body.model_dump(exclude_unset=True)))
    )


@app.post(PREFIX + "/auth/password", operation_id="AUTH-07")
def password(body: s.Password, ctx=D):
    conn, user, _ = ctx
    user = c.require(db.one(conn, db.users, user["id"], lock=True))
    if not c.password_hash.verify(body.old_password, user["password_hash"]):
        raise c.Problem("OLD_PASSWORD_INCORRECT", "原密码不正确")
    db.update(
        conn,
        db.users,
        user["id"],
        password_hash=c.password_hash.hash(body.new_password),
        auth_version=user["auth_version"] + 1,
    )
    conn.execute(db.sessions.update().where(db.sessions.c.owner_id == user["id"]).values(revoked_at=db.now()))
    response = c.envelope({"password_changed": True, "reauthentication_required": True})
    response.delete_cookie(c.COOKIE, path="/")
    return response


@app.get(PREFIX + "/models", operation_id="M-01")
def models(ctx=D):
    items = [
        {
            "id": key,
            "display_name": value.get("display_name", key.replace("-", " ")),
            "supports_images": bool(value.get("supports_images")),
            "enabled": True,
        }
        for key, value in settings().models().items()
        if value.get("enabled")
    ]
    return c.envelope({"items": items, "default_model_id": settings().default_model()})


@app.get(PREFIX + "/skills", operation_id="SK-01")
def list_skills(ctx=D):
    return c.envelope({"items": [skills.public(value) for value in skills.catalog().values()]})


@app.get(PREFIX + "/mcp/servers", operation_id="MC-01")
def list_mcp(ctx=D):
    return c.envelope({"items": [mcp_tools.descriptor(k, v) for k, v in settings().mcp_servers().items()]})


@app.post(PREFIX + "/mcp/servers/{server_id}/check", operation_id="MC-02")
async def check_mcp(server_id: str, request: Request):
    with db.engine.begin() as conn:
        c.authenticate(conn, request)
    servers = settings().mcp_servers()
    if server_id not in servers:
        raise c.Problem("RESOURCE_NOT_FOUND", "MCP 服务不存在", 404)
    return c.envelope(await mcp_tools.check(server_id, servers[server_id]))


def owned_mcp(request, agent_id):
    with db.engine.begin() as conn:
        user, _ = c.authenticate(conn, request)
        agent = c.require(db.one(conn, db.agents, agent_id, user["id"]))
        return mcp_tools.selected(agent["mcp_server_ids"])


@app.get(PREFIX + "/agents/{agent_id}/mcp/tools", operation_id="MC-03")
async def list_mcp_tools(agent_id: UUID, request: Request):
    servers = owned_mcp(request, agent_id)
    items = []
    try:
        for ident, server in servers.items():
            async with mcp_tools.connect(server) as session:
                items.extend(await mcp_tools.discover(session, ident, server))
    except asyncio.CancelledError:
        raise
    except Exception:
        raise c.Problem("MCP_UNAVAILABLE", "MCP 工具目录暂时不可用", 503) from None
    return c.envelope({"items": items})


@app.post(PREFIX + "/agents/{agent_id}/mcp/tools/call", operation_id="MC-04")
async def call_mcp_tool(agent_id: UUID, body: s.MCPCall, request: Request):
    servers = owned_mcp(request, agent_id)
    if body.server_id not in servers or body.tool_name not in servers[body.server_id]["allowed_tools"]:
        raise c.Problem("MCP_TOOL_FORBIDDEN", "此智能体未启用该工具", 403)
    server = servers[body.server_id]
    try:
        async with mcp_tools.connect(server) as session:
            tools = await mcp_tools.discover(session, body.server_id, server)
            tool = next((t for t in tools if t["name"] == body.tool_name), None)
            if tool is None:
                raise c.Problem("MCP_UNAVAILABLE", "工具不在当前目录中", 503)
            result = await mcp_tools.invoke(session, server, tool, body.arguments)
    except (asyncio.CancelledError, c.Problem):
        raise
    except Exception:
        raise c.Problem("MCP_UNAVAILABLE", "MCP 工具暂时不可用", 503) from None
    return c.envelope(result)


def capability_selections(data, defaults=False):
    catalogs = {"mcp_server_ids": settings().mcp_servers(), "skill_ids": skills.catalog()}
    for field, catalog in catalogs.items():
        if field not in data:
            continue
        ids = data[field]
        if ids is None and defaults:
            ids = [k for k, v in catalog.items() if v.get("default_enabled") and v.get("enabled", True)]
        if ids is None or len(set(ids)) != len(ids):
            raise c.Problem("VALIDATION_ERROR", "MCP 和技能选择不能为空值或重复", 422)
        if any(ident not in catalog or not catalog[ident].get("enabled", True) for ident in ids):
            raise c.Problem("CAPABILITY_UNAVAILABLE", "所选 MCP 或技能不在后台开放目录中", 422)
        data[field] = ids
    return data


def bind_libraries(conn, agent, user, ids):
    for ident in sorted(ids):
        c.require(db.one(conn, db.knowledge_bases, ident, user, lock=True))
    conn.execute(db.agent_knowledge_bases.delete().where(db.agent_knowledge_bases.c.agent_id == agent))
    for ident in ids:
        conn.execute(
            db.agent_knowledge_bases.insert().values(agent_id=agent, knowledge_base_id=ident, owner_id=user)
        )


@app.post(PREFIX + "/agents", operation_id="A-02")
def create_agent(body: s.AgentCreate, ctx=D):
    conn, user, _ = ctx
    config = c.model_config()
    data = capability_selections(body.model_dump(exclude={"knowledge_base_ids"}), defaults=True)
    row = db.insert(
        conn,
        db.agents,
        owner_id=user["id"],
        model_id=config["model_id"],
        **data,
    )
    bind_libraries(conn, row["id"], user["id"], body.knowledge_base_ids)
    return c.envelope(c.agent(conn, row), 201)


@app.patch(PREFIX + "/agents/{agent_id}", operation_id="A-04")
def update_agent(agent_id: UUID, body: s.AgentPatch, ctx=D):
    conn, user, _ = ctx
    if body.knowledge_base_ids is not None:
        for ident in sorted(body.knowledge_base_ids):
            c.require(db.one(conn, db.knowledge_bases, ident, user["id"], lock=True))
    row = c.require(db.one(conn, db.agents, agent_id, user["id"], lock=True))
    data = capability_selections(body.model_dump(exclude_unset=True))
    if "model_id" in data:
        c.model_config(data["model_id"])
    if "knowledge_base_ids" in data:
        bind_libraries(conn, agent_id, user["id"], data.pop("knowledge_base_ids"))
    row = db.update(conn, db.agents, agent_id, **data, config_version=row["config_version"] + 1)
    return c.envelope(c.agent(conn, row))


@app.post(PREFIX + "/prompt-tasks", operation_id="A-07")
def prompt_task(body: s.Prompt, request: Request, ctx=D):
    conn, user, _ = ctx

    def action():
        row = db.insert(
            conn,
            db.tasks,
            owner_id=user["id"],
            kind="prompt_" + body.mode,
            phase="queued",
            input_snapshot=body.model_dump(),
            config_snapshot=c.model_config(),
        )
        return {"task": c.task(conn, row)}, 202, [["tasks", str(row["id"])]]

    return c.idem(conn, request, user["id"], body.model_dump(), action)


def converter(conn, t, row):
    if t is db.agents:
        return c.agent(conn, row)
    if t is db.knowledge_bases:
        return c.knowledge_base(conn, row)
    if t is db.files:
        return c.file(conn, row)
    if t is db.messages:
        return c.message(conn, row)
    if t is db.learning_plans:
        return c.plan(conn, row)
    if t is db.exercises:
        return c.exercise(row)
    if t is db.tasks:
        return c.task(conn, row)
    return c.public(
        row, "id", "agent_id", "name", "has_images", "last_accessed_at", "created_at", "updated_at"
    )


def path_id(request):
    try:
        return UUID(next(iter(request.path_params.values())))
    except (ValueError, TypeError):
        raise c.Problem("VALIDATION_ERROR", "资源标识应为 UUID")


def list_route(path, t, operation, parent_t=None, parent_col=None):
    def endpoint(request: Request, cursor: str | None = None, limit: int = Query(20, ge=1, le=100), ctx=D):
        conn, user, _ = ctx
        clauses = []
        if parent_t is not None:
            ident = path_id(request)
            c.require(db.one(conn, parent_t, ident, user["id"]))
            clauses = [t.c[parent_col] == ident]
        order = (
            sa.func.coalesce(t.c.last_accessed_at, t.c.created_at)
            if t is db.conversations
            else t.c.sequence_no
            if t is db.messages
            else None
        )
        rows, next_cursor = c.paginate(conn, t, user["id"], request.url.path, clauses, cursor, limit, order)
        return c.envelope({"items": [converter(conn, t, row) for row in rows], "next_cursor": next_cursor})

    app.get(PREFIX + path, operation_id=operation)(endpoint)


def get_route(path, t, operation):
    def endpoint(request: Request, ctx=D):
        conn, user, _ = ctx
        ident = path_id(request)
        row = c.require(db.one(conn, t, ident, user["id"]))
        if t is db.tasks and row["kind"] == "storage_cleanup":
            raise c.Problem("RESOURCE_NOT_FOUND", "资源不存在或不可访问", 404)
        return c.envelope(converter(conn, t, row))

    app.get(PREFIX + path, operation_id=operation)(endpoint)


for args in [
    ("/agents", db.agents, "A-01"),
    ("/agents/{agent_id}/conversations", db.conversations, "C-01", db.agents, "agent_id"),
    ("/conversations/{conversation_id}/messages", db.messages, "C-08", db.conversations, "conversation_id"),
    ("/agents/{agent_id}/plans", db.learning_plans, "P-01", db.agents, "agent_id"),
    ("/knowledge-bases", db.knowledge_bases, "K-01"),
    ("/knowledge-bases/{knowledge_base_id}/files", db.files, "F-01", db.knowledge_bases, "knowledge_base_id"),
]:
    list_route(*args)
for args in [
    ("/agents/{agent_id}", db.agents, "A-03"),
    ("/conversations/{conversation_id}", db.conversations, "C-03"),
    ("/plans/{plan_id}", db.learning_plans, "P-02"),
    ("/knowledge-bases/{knowledge_base_id}", db.knowledge_bases, "K-03"),
    ("/files/{file_id}", db.files, "F-03"),
    ("/exercises/{exercise_id}", db.exercises, "Q-01"),
    ("/tasks/{task_id}", db.tasks, "T-01"),
]:
    get_route(*args)


@app.post(PREFIX + "/agents/{agent_id}/conversations", operation_id="C-02")
def create_conversation(agent_id: UUID, body: s.ConversationCreate, ctx=D):
    conn, user, _ = ctx
    c.require(db.one(conn, db.agents, agent_id, user["id"]))
    row = db.insert(conn, db.conversations, owner_id=user["id"], agent_id=agent_id, name=body.name)
    return c.envelope(converter(conn, db.conversations, row), 201)


@app.post(PREFIX + "/conversations/{conversation_id}/access", operation_id="C-06")
def access(conversation_id: UUID, body: s.Empty, ctx=D):
    conn, user, _ = ctx
    c.require(db.one(conn, db.conversations, conversation_id, user["id"]))
    return c.envelope(
        converter(
            conn,
            db.conversations,
            db.update(conn, db.conversations, conversation_id, last_accessed_at=db.now()),
        )
    )


@app.post(PREFIX + "/knowledge-bases", operation_id="K-02")
def create_library(body: s.Name, ctx=D):
    conn, user, _ = ctx
    return c.envelope(
        c.knowledge_base(conn, db.insert(conn, db.knowledge_bases, owner_id=user["id"], name=body.name)), 201
    )


def rename_route(path, t, operation):
    def endpoint(request: Request, body: s.Name, ctx=D):
        conn, user, _ = ctx
        ident = path_id(request)
        c.require(db.one(conn, t, ident, user["id"], lock=True))
        return c.envelope(converter(conn, t, db.update(conn, t, ident, name=body.name)))

    app.patch(PREFIX + path, operation_id=operation)(endpoint)


rename_route("/conversations/{conversation_id}", db.conversations, "C-04")
rename_route("/plans/{plan_id}", db.learning_plans, "P-03")
rename_route("/knowledge-bases/{knowledge_base_id}", db.knowledge_bases, "K-04")


def accept_chat(conn, owner, conversation_id, body, previous=None, answer=None):
    initial = c.require(db.one(conn, db.conversations, conversation_id, owner))
    agent = c.require(db.one(conn, db.agents, initial["agent_id"], owner, lock=True))
    conversation = c.require(db.one(conn, db.conversations, conversation_id, owner, lock=True))
    if conn.execute(
        sa.select(db.tasks.c.id).where(
            db.tasks.c.conversation_id == conversation_id, db.tasks.c.status.in_(["queued", "running"])
        )
    ).first():
        raise c.Problem("CONVERSATION_BUSY", "此对话正在处理请求，请等待或停止当前任务", 409)
    config = c.agent_config(conn, agent)
    skill = None
    if body.skill_id:
        skill = skills.catalog().get(body.skill_id)
        if not skill or body.skill_id not in agent["skill_ids"]:
            raise c.Problem("SKILL_UNAVAILABLE", "此技能已不可用或尚未在智能体中开启", 409)
        config["skill"] = skill
    from .providers import input_estimate

    initial_tokens = input_estimate(body.content_text) + input_estimate(skill["body"] if skill else "")
    if initial_tokens > config["input_token_budget"]:
        raise c.Problem("CONTEXT_LIMIT_EXCEEDED", "当前输入超过模型容量，请缩短内容或新建对话", 409)
    target = None
    if body.target_plan_id:
        p = c.require(db.one(conn, db.learning_plans, body.target_plan_id, owner))
        if p["agent_id"] != agent["id"]:
            raise c.Problem("RESOURCE_NOT_FOUND", "目标不属于当前智能体", 404)
        if p["content_version"] != body.expected_plan_version:
            raise c.Problem("PLAN_VERSION_CONFLICT", "计划已更新，请先查看最新版", 409)
        target = {
            "id": str(p["id"]),
            "name_snapshot": p["name"],
            "expected_plan_version": p["content_version"],
        }
    images = []
    for ident in body.attachment_ids:
        a = c.require(db.one(conn, db.attachments, ident, owner, lock=True))
        if a["conversation_id"] != conversation_id or (
            not previous and (a["state"] != "staged" or a["expires_at"] <= db.now())
        ):
            raise c.Problem("RESOURCE_NOT_FOUND", "图片不可用于此消息", 404)
        images.append(a)
    all_images = list(
        conn.execute(
            sa.select(db.attachments).where(
                db.attachments.c.conversation_id == conversation_id, db.attachments.c.state == "bound"
            )
        ).mappings()
    )
    by_id = {a["id"]: a for a in [*all_images, *images]}
    if by_id and not config["supports_images"]:
        raise c.Problem("MODEL_IMAGE_UNSUPPORTED", "当前模型不支持此对话中的图片，请手动选择图片模型", 409)
    if len(by_id) > config.get("max_images", 0) or sum(a["byte_size"] for a in by_id.values()) > config.get(
        "max_image_bytes", 0
    ):
        raise c.Problem("CONTEXT_LIMIT_EXCEEDED", "历史图片超过模型容量，请新建对话", 409)
    seq = conversation["next_sequence_no"]
    if previous:
        user_message = c.require(db.one(conn, db.messages, previous["user_message_id"], owner))
        attempt = previous["attempt_no"] + 1
    else:
        user_message = db.insert(
            conn,
            db.messages,
            owner_id=owner,
            agent_id=agent["id"],
            conversation_id=conversation_id,
            sequence_no=seq,
            role="user",
            origin="answer_request" if answer else "user_input",
            content_text=body.content_text,
            skill={k: skill[k] for k in ("id", "name", "description")} if skill else None,
            answer_exercise_id=answer["id"] if answer else None,
        )
        seq += 1
        attempt = 1
        for i, a in enumerate(images):
            conn.execute(
                db.message_attachments.insert().values(
                    message_id=user_message["id"],
                    attachment_id=a["id"],
                    owner_id=owner,
                    agent_id=agent["id"],
                    conversation_id=conversation_id,
                    position=i,
                )
            )
            db.update(conn, db.attachments, a["id"], state="bound", expires_at=None)
    inp = {
        "content_text": body.content_text,
        "skill_id": body.skill_id,
        "attachment_ids": [str(x) for x in body.attachment_ids],
        "answer_exercise_id": str(answer["id"])
        if answer
        else previous["input_snapshot"].get("answer_exercise_id")
        if previous
        else None,
        "target_plan": target,
    }
    if previous and previous["input_snapshot"].get("feedback_exercise_id"):
        inp["feedback_exercise_id"] = previous["input_snapshot"]["feedback_exercise_id"]
    t = db.insert(
        conn,
        db.tasks,
        owner_id=owner,
        kind="chat",
        phase="queued",
        agent_id=agent["id"],
        conversation_id=conversation_id,
        user_message_id=user_message["id"],
        target_plan_id=body.target_plan_id,
        base_plan_version=body.expected_plan_version,
        attempt_no=attempt,
        previous_task_id=previous["id"] if previous else None,
        input_snapshot=inp,
        config_snapshot=jsonable_encoder(config),
    )
    assistant = db.insert(
        conn,
        db.messages,
        owner_id=owner,
        agent_id=agent["id"],
        conversation_id=conversation_id,
        sequence_no=seq,
        role="assistant",
        origin="assistant",
        user_message_id=user_message["id"],
        task_id=t["id"],
        response_status="queued",
    )
    t = db.update(conn, db.tasks, t["id"], assistant_message_id=assistant["id"])
    db.update(
        conn,
        db.conversations,
        conversation_id,
        next_sequence_no=seq + 1,
        has_images=conversation["has_images"] or bool(images),
    )
    return (
        {"user_message": c.message(conn, user_message), "task": c.task(conn, t)},
        202,
        [["tasks", str(t["id"])], ["messages", str(user_message["id"])]],
    )


@app.post(PREFIX + "/conversations/{conversation_id}/messages", operation_id="C-09")
def send(conversation_id: UUID, body: s.Send, request: Request, ctx=D):
    conn, user, _ = ctx
    return c.idem(
        conn,
        request,
        user["id"],
        body.model_dump(),
        lambda: accept_chat(conn, user["id"], conversation_id, body),
    )


@app.post(PREFIX + "/messages/{message_id}/retry", operation_id="C-10")
def retry(message_id: UUID, body: s.Retry, request: Request, ctx=D):
    conn, user, _ = ctx

    def action():
        original = c.require(db.one(conn, db.messages, message_id, user["id"]))
        previous = c.require(db.one(conn, db.tasks, body.previous_task_id, user["id"], lock=True))
        latest = conn.execute(
            sa.select(db.tasks.c.id)
            .where(db.tasks.c.user_message_id == message_id)
            .order_by(db.tasks.c.attempt_no.desc())
            .limit(1)
        ).scalar()
        if (
            previous["user_message_id"] != message_id
            or previous["status"] not in {"failed", "stopped"}
            or latest != previous["id"]
        ):
            raise c.Problem("TASK_NOT_RETRYABLE", "只能重试最新的失败或停止请求", 409)
        inp = previous["input_snapshot"]
        target = inp.get("target_plan")
        version = None
        target_id = None
        if target:
            p = c.require(db.one(conn, db.learning_plans, UUID(target["id"]), user["id"]))
            target_id = p["id"]
            version = body.expected_plan_version or p["content_version"]
            if previous["error_code"] == "PLAN_VERSION_CONFLICT" and body.expected_plan_version is None:
                raise c.Problem("PLAN_VERSION_CONFLICT", "请先查看最新计划并确认版本", 409)
        message = s.Send(
            content_text=inp["content_text"],
            skill_id=inp.get("skill_id"),
            attachment_ids=inp["attachment_ids"],
            target_plan_id=target_id,
            expected_plan_version=version,
        )
        return accept_chat(conn, user["id"], original["conversation_id"], message, previous)

    return c.idem(conn, request, user["id"], body.model_dump(), action)


@app.post(PREFIX + "/exercises/{exercise_id}/answer", operation_id="Q-02")
def answer(exercise_id: UUID, body: s.Empty, request: Request, ctx=D):
    conn, user, _ = ctx
    e = c.require(db.one(conn, db.exercises, exercise_id, user["id"]))
    msg = s.Send(content_text=f"请展示这道题的答案和解析（题目标识 {exercise_id}）：\n{e['question_text']}")
    return c.idem(
        conn,
        request,
        user["id"],
        {},
        lambda: accept_chat(conn, user["id"], e["conversation_id"], msg, answer=e),
    )


@app.post(PREFIX + "/conversations/{conversation_id}/images", operation_id="I-01")
async def upload_image(conversation_id: UUID, request: Request, file: UploadFile = File(...)):
    with db.engine.begin() as conn:
        user, _ = c.authenticate(conn, request)
        owner = user["id"]
    data = await file.read(10485761)
    if not data or len(data) > 10485760:
        raise c.Problem("VALIDATION_ERROR", "图片应大于 0 且不超过 10 MiB")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise c.Problem("VALIDATION_ERROR", "仅支持 JPG、PNG、WebP 静态图片")
    try:
        im = Image.open(io.BytesIO(data))
        im.verify()
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.format not in {"JPEG", "PNG", "WEBP"} or getattr(im, "n_frames", 1) != 1:
            raise ValueError()
        width, height = im.size
        media = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[im.format]
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise c.Problem("VALIDATION_ERROR", "无法读取静态图片")
    key = None
    try:
        with db.engine.begin() as conn:
            c.authenticate(conn, request)

            def action():
                nonlocal key
                conversation = c.require(db.one(conn, db.conversations, conversation_id, owner, lock=True))
                key = c.store_bytes(data)
                row = db.insert(
                    conn,
                    db.attachments,
                    owner_id=owner,
                    agent_id=conversation["agent_id"],
                    conversation_id=conversation_id,
                    original_name=Path(file.filename or "图片").name[:255],
                    storage_key=key,
                    media_type=media,
                    byte_size=len(data),
                    width=width,
                    height=height,
                    sha256=hashlib.sha256(data).digest(),
                    expires_at=db.now() + timedelta(hours=24),
                )
                return c.attachment(row), 201, [["attachments", str(row["id"])]]

            return c.idem(
                conn,
                request,
                owner,
                {"sha256": hashlib.sha256(data).hexdigest(), "name": file.filename},
                action,
            )
    except Exception:
        if key:
            c.storage_path(key).unlink(missing_ok=True)
        raise


@app.get(PREFIX + "/attachments/{attachment_id}/content", operation_id="I-02")
def image(attachment_id: UUID, ctx=D):
    a = c.require(db.one(ctx[0], db.attachments, attachment_id, ctx[1]["id"]))
    path = c.storage_path(a["storage_key"])
    if not path.exists():
        raise c.Problem("RESOURCE_NOT_FOUND", "图片不可用", 404)
    return FileResponse(path, media_type=a["media_type"], headers={"Cache-Control": "private, no-store"})


@app.post(PREFIX + "/knowledge-bases/{knowledge_base_id}/files", operation_id="F-02")
async def upload_files(knowledge_base_id: UUID, request: Request, files: list[UploadFile] = File(...)):
    if not 1 <= len(files) <= 10:
        raise c.Problem("VALIDATION_ERROR", "每批请选择 1–10 个文件")
    with db.engine.begin() as conn:
        user, _ = c.authenticate(conn, request)
        owner = user["id"]
    entries = []
    for f in files:
        data = await f.read(52428801)
        ext = Path(f.filename or "").suffix.lower()
        err = None
        if not data or len(data) > 52428800:
            err = "单个文件应大于 0 且不超过 50 MiB"
        elif ext not in {".pdf", ".docx", ".txt"}:
            err = "仅支持 PDF、DOCX、TXT"
        elif ext == ".pdf" and not data.lstrip().startswith(b"%PDF-"):
            err = "文件内容不是 PDF"
        elif ext == ".docx" and not data.startswith(b"PK"):
            err = "文件内容不是 DOCX"
        entries.append((Path(f.filename or "文件").name[:255], ext, data, err))
    written = []
    try:
        with db.engine.begin() as conn:
            c.authenticate(conn, request)

            def action():
                c.require(db.one(conn, db.knowledge_bases, knowledge_base_id, owner, lock=True))
                result = []
                refs = []
                for name, ext, data, err in entries:
                    if err:
                        result.append(
                            {"original_name": name, "error": {"code": "VALIDATION_ERROR", "message": err}}
                        )
                        continue
                    key = c.store_bytes(data)
                    written.append(key)
                    row = db.insert(
                        conn,
                        db.files,
                        owner_id=owner,
                        knowledge_base_id=knowledge_base_id,
                        original_name=name,
                        extension=ext,
                        media_type={
                            ".pdf": "application/pdf",
                            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            ".txt": "text/plain",
                        }[ext],
                        storage_key=key,
                        sha256=hashlib.sha256(data).digest(),
                        byte_size=len(data),
                    )
                    t = db.insert(
                        conn,
                        db.tasks,
                        owner_id=owner,
                        kind="file_process",
                        phase="queued",
                        file_id=row["id"],
                        timeout_seconds=600,
                        input_snapshot={"file_id": str(row["id"])},
                    )
                    result.append({"file": c.file(conn, row), "task": c.task(conn, t)})
                    refs.append(["files", str(row["id"])])
                return {"items": result}, 202 if refs else 200, refs

            payload = [
                {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "error": err}
                for name, ext, data, err in entries
            ]
            return c.idem(conn, request, owner, payload, action)
    except Exception:
        for key in written:
            c.storage_path(key).unlink(missing_ok=True)
        raise


@app.post(PREFIX + "/files/{file_id}/reprocess", operation_id="F-05")
def reprocess(file_id: UUID, body: s.Empty, request: Request, ctx=D):
    conn, user, _ = ctx

    def action():
        row = c.require(db.one(conn, db.files, file_id, user["id"], lock=True))
        if row["status"] != "failed":
            raise c.Problem("TASK_NOT_RETRYABLE", "仅失败文件可重新处理", 409)
        old = (
            conn.execute(
                sa.select(db.tasks)
                .where(db.tasks.c.file_id == file_id)
                .order_by(db.tasks.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        row = db.update(conn, db.files, file_id, status="processing", error_code=None, error_message=None)
        t = db.insert(
            conn,
            db.tasks,
            owner_id=user["id"],
            kind="file_process",
            phase="queued",
            file_id=file_id,
            timeout_seconds=600,
            previous_task_id=old["id"],
            attempt_no=old["attempt_no"] + 1,
            input_snapshot={"file_id": str(file_id)},
        )
        return (
            {"file": c.file(conn, row), "task": c.task(conn, t)},
            202,
            [["files", str(file_id)], ["tasks", str(t["id"])]],
        )

    return c.idem(conn, request, user["id"], {}, action)


def allowed_source(conn, ident, owner):
    row = c.require(db.one(conn, db.sources, ident, owner))
    has_message = conn.execute(
        sa.select(db.message_sources.c.source_id).where(
            db.message_sources.c.source_id == ident, db.message_sources.c.owner_id == owner
        )
    ).first()
    has_plan = conn.execute(
        sa.select(db.plan_sources.c.source_id).where(
            db.plan_sources.c.source_id == ident, db.plan_sources.c.owner_id == owner
        )
    ).first()
    if not has_message and not has_plan:
        raise c.Problem("RESOURCE_NOT_FOUND", "来源不可访问", 404)
    return row


def preview_data(conn, file_id, owner, cursor, limit, source_id=None):
    row = c.require(db.one(conn, db.files, file_id, owner))
    if row["status"] != "ready":
        raise c.Problem("FILE_NOT_READY", "文件尚未准备好", 409)
    locator = None
    focus_index = None
    if source_id:
        source = allowed_source(conn, source_id, owner)
        if source["file_id"] != file_id:
            raise c.Problem("RESOURCE_NOT_FOUND", "来源不属于当前文件", 404)
        locator = source["locator_snapshot"]
        chunk = db.one(conn, db.document_chunks, source["chunk_id"], owner) if source["chunk_id"] else None
        if chunk and chunk["file_id"] == file_id and chunk["parse_version"] == row["parse_version"]:
            focus_index = chunk["chunk_index"]
    clauses = [
        db.document_chunks.c.file_id == file_id,
        db.document_chunks.c.parse_version == row["parse_version"],
    ]
    if focus_index is not None:
        clauses.append(db.document_chunks.c.chunk_index >= focus_index)
    rows, next_cursor = c.paginate(
        conn,
        db.document_chunks,
        owner,
        f"preview:{file_id}:{row['parse_version']}:{source_id or ''}",
        clauses,
        cursor,
        limit,
        db.document_chunks.c.chunk_index,
    )
    blocks = []
    previous_end = 0
    if rows and not source_id and rows[0]["chunk_index"] > 0:
        previous_end = (
            conn.execute(
                sa.select(sa.func.max(db.document_chunks.c.char_end)).where(
                    db.document_chunks.c.file_id == file_id,
                    db.document_chunks.c.parse_version == row["parse_version"],
                    db.document_chunks.c.chunk_index < rows[0]["chunk_index"],
                )
            ).scalar()
            or 0
        )
    for r in rows:
        start = max(previous_end, r["char_start"])
        blocks.append(
            {
                "id": r["id"],
                "text": row["text_snapshot"][start : r["char_end"]],
                "locator": {**r["locator"], "char_start": start},
            }
        )
        previous_end = r["char_end"]
    return {
        "file_id": file_id,
        "name": row["original_name"],
        "parse_version": row["parse_version"],
        "blocks": blocks,
        "next_cursor": next_cursor,
        "focus_locator": locator,
        "warning": "仅展示提取文字，不还原原文排版。",
    }


@app.get(PREFIX + "/files/{file_id}/preview", operation_id="F-04")
def preview(
    file_id: UUID,
    source_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    ctx=D,
):
    return c.envelope(preview_data(ctx[0], file_id, ctx[1]["id"], cursor, limit, source_id))


@app.get(PREFIX + "/sources/{source_id}", operation_id="S-01")
def source(source_id: UUID, ctx=D):
    return c.envelope(c.source(allowed_source(ctx[0], source_id, ctx[1]["id"])))


@app.get(PREFIX + "/sources/{source_id}/preview", operation_id="S-02")
def source_preview(source_id: UUID, cursor: str | None = None, limit: int = Query(20, ge=1, le=100), ctx=D):
    row = allowed_source(ctx[0], source_id, ctx[1]["id"])
    if row["kind"] != "file":
        raise c.Problem("SOURCE_NOT_INTERNAL", "网络来源请打开原网页")
    if not row["file_id"]:
        raise c.Problem("SOURCE_DELETED", "来源已删除", 410)
    return c.envelope(preview_data(ctx[0], row["file_id"], ctx[1]["id"], cursor, limit, source_id))


def stop_row(conn, row):
    if row["status"] in c.TERMINAL:
        return row
    data = {"cancel_requested": True, "revision": row["revision"] + 1}
    if row["status"] == "queued":
        data.update(status="stopped", finished_at=db.now())
    row = db.update(conn, db.tasks, row["id"], **data)
    if row["assistant_message_id"] and row["status"] == "stopped":
        db.update(conn, db.messages, row["assistant_message_id"], response_status="stopped")
    return row


@app.post(PREFIX + "/tasks/{task_id}/stop", operation_id="T-03")
def stop(task_id: UUID, body: s.Empty, ctx=D):
    conn, user, _ = ctx
    row = c.require(db.one(conn, db.tasks, task_id, user["id"], lock=True))
    if row["kind"] == "storage_cleanup":
        raise c.Problem("RESOURCE_NOT_FOUND", "资源不存在或不可访问", 404)
    if row["kind"] not in {"chat", "prompt_generate", "prompt_polish"}:
        raise c.Problem("TASK_STOP_NOT_SUPPORTED", "文件处理请通过删除文件取消", 409)
    return c.envelope(c.task(conn, stop_row(conn, row)))


@app.post(PREFIX + "/tasks/{task_id}/retry", operation_id="T-04")
def retry_prompt(task_id: UUID, body: s.Empty, request: Request, ctx=D):
    conn, user, _ = ctx

    def action():
        row = c.require(db.one(conn, db.tasks, task_id, user["id"], lock=True))
        if row["kind"] not in {"prompt_generate", "prompt_polish"} or row["status"] not in {
            "failed",
            "stopped",
        }:
            raise c.Problem("TASK_NOT_RETRYABLE", "此任务不可从此入口重试", 409)
        if conn.execute(sa.select(db.tasks.c.id).where(db.tasks.c.previous_task_id == task_id)).first():
            raise c.Problem("TASK_NOT_RETRYABLE", "此任务已有后续尝试", 409)
        t = db.insert(
            conn,
            db.tasks,
            owner_id=user["id"],
            kind=row["kind"],
            phase="queued",
            previous_task_id=task_id,
            attempt_no=row["attempt_no"] + 1,
            input_snapshot=row["input_snapshot"],
            config_snapshot=c.model_config(),
        )
        return {"task": c.task(conn, t)}, 202, [["tasks", str(t["id"])]]

    return c.idem(conn, request, user["id"], {}, action)


@app.get(PREFIX + "/tasks/{task_id}/events", operation_id="T-02")
async def events(task_id: UUID, request: Request, after_revision: int | None = None):
    with db.engine.begin() as conn:
        user, _ = c.authenticate(conn, request)
        owner = user["id"]
        row = c.require(db.one(conn, db.tasks, task_id, owner))
        if row["kind"] == "storage_cleanup":
            raise c.Problem("RESOURCE_NOT_FOUND", "资源不存在或不可访问", 404)
        try:
            revision = (
                after_revision
                if after_revision is not None
                else int(request.headers.get("last-event-id", "0"))
            )
        except ValueError:
            raise c.Problem("INVALID_REVISION", "任务版本无效")
        if revision < 0 or revision > row["revision"]:
            raise c.Problem("INVALID_REVISION", "任务版本无效")

    async def stream():
        last = -1
        heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            with db.engine.begin() as conn:
                try:
                    c.authenticate(conn, request)
                except c.Problem:
                    return
                row = db.one(conn, db.tasks, task_id, owner)
                if not row:
                    return
                snapshot = jsonable_encoder(c.task(conn, row))
            first = last == -1
            if first or row["revision"] > last:
                event = "snapshot" if first else "progress"
                yield f"id: {row['revision']}\nevent: {event}\ndata: {json.dumps({'data': snapshot, 'request_id': request.state.request_id}, ensure_ascii=False)}\n\n"
                last = row["revision"]
                heartbeat = asyncio.get_running_loop().time()
            if row["status"] in c.TERMINAL:
                event = {"succeeded": "completed", "failed": "failed", "stopped": "stopped"}[row["status"]]
                yield f"id: {last}\nevent: {event}\ndata: {json.dumps({'data': snapshot, 'request_id': request.state.request_id}, ensure_ascii=False)}\n\n"
                return
            if asyncio.get_running_loop().time() - heartbeat >= 15:
                yield f"event: heartbeat\ndata: {json.dumps({'data': snapshot, 'request_id': request.state.request_id}, ensure_ascii=False)}\n\n"
                heartbeat = asyncio.get_running_loop().time()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def deletion_targets(conn, t, ident, owner):
    files = []
    images = []
    task_filter = []
    counts = {}
    retained = []
    if t is db.agents:
        conv_ids = list(
            conn.execute(
                sa.select(db.conversations.c.id).where(db.conversations.c.agent_id == ident)
            ).scalars()
        )
        images = list(
            conn.execute(
                sa.select(db.attachments).where(db.attachments.c.conversation_id.in_(conv_ids))
            ).mappings()
        )
        counts = {
            "conversations": len(conv_ids),
            "images": len(images),
            "plans": conn.execute(
                sa.select(sa.func.count())
                .select_from(db.learning_plans)
                .where(db.learning_plans.c.agent_id == ident)
            ).scalar(),
        }
        task_filter = [db.tasks.c.agent_id == ident]
        retained = ["独立知识库及其文件"]
    elif t is db.conversations:
        images = list(
            conn.execute(
                sa.select(db.attachments).where(db.attachments.c.conversation_id == ident)
            ).mappings()
        )
        counts = {"images": len(images)}
        task_filter = [db.tasks.c.conversation_id == ident]
        retained = ["独立学习计划"]
    elif t is db.learning_plans:
        task_filter = [db.tasks.c.target_plan_id == ident]
        retained = ["历史聊天"]
    elif t is db.knowledge_bases:
        files = list(
            conn.execute(sa.select(db.files).where(db.files.c.knowledge_base_id == ident)).mappings()
        )
        counts = {
            "files": len(files),
            "agents": conn.execute(
                sa.select(sa.func.count())
                .select_from(db.agent_knowledge_bases)
                .where(db.agent_knowledge_bases.c.knowledge_base_id == ident)
            ).scalar(),
        }
        task_filter = [db.tasks.c.file_id.in_([f["id"] for f in files])]
        retained = ["历史回答与学习计划正文，内部引用将失效"]
    elif t is db.files:
        files = [c.require(db.one(conn, t, ident, owner))]
        task_filter = [db.tasks.c.file_id == ident]
        retained = ["历史正文，内部引用将失效"]
    else:
        images = [c.require(db.one(conn, t, ident, owner))]
    return files, images, task_filter, counts, retained


def deletion_route(path, t, operation, impact_id=None):
    def impact(request: Request, ctx=D):
        conn, user, _ = ctx
        ident = path_id(request)
        c.require(db.one(conn, t, ident, user["id"]))
        *_, counts, retained = deletion_targets(conn, t, ident, user["id"])
        return c.envelope(
            {
                "kind": t.name,
                "counts": counts,
                "retained": retained,
                "warning": "删除后无法恢复，相关未完成任务会取消。",
            }
        )

    def remove(request: Request, ctx=D):
        conn, user, _ = ctx
        ident = path_id(request)
        row = c.require(db.one(conn, t, ident, user["id"]))
        if t is db.attachments and row["state"] != "staged":
            raise c.Problem("ATTACHMENT_BOUND", "历史图片随对话删除", 409)
        files, images, filters, _, _ = deletion_targets(conn, t, ident, user["id"])
        if filters:
            active = (
                conn.execute(
                    sa.select(db.tasks)
                    .where(
                        db.tasks.c.owner_id == user["id"],
                        *filters,
                        db.tasks.c.status.in_(["queued", "running"]),
                    )
                    .order_by(db.tasks.c.id)
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            for task in active:
                stopped = stop_row(conn, task)
                if stopped["status"] == "running":
                    db.update(
                        conn,
                        db.tasks,
                        task["id"],
                        status="stopped",
                        finished_at=db.now(),
                        revision=stopped["revision"] + 1,
                    )
                    if task["assistant_message_id"]:
                        db.update(conn, db.messages, task["assistant_message_id"], response_status="stopped")
        c.require(db.one(conn, t, ident, user["id"], lock=True))
        if t is db.knowledge_bases:
            affected = list(
                conn.execute(
                    sa.select(db.agent_knowledge_bases.c.agent_id).where(
                        db.agent_knowledge_bases.c.knowledge_base_id == ident
                    )
                ).scalars()
            )
            conn.execute(
                db.agents.update()
                .where(db.agents.c.id.in_(affected))
                .values(config_version=db.agents.c.config_version + 1)
            )
        for item in [*files, *images]:
            c.cleanup(conn, user["id"], item["storage_key"])
        conn.execute(t.delete().where(t.c.id == ident, t.c.owner_id == user["id"]))
        return c.envelope({"deleted_id": ident})

    app.delete(PREFIX + path, operation_id=operation)(remove)
    if impact_id:
        app.get(PREFIX + path + "/delete-impact", operation_id=impact_id)(impact)


for args in [
    ("/agents/{agent_id}", db.agents, "A-05", "A-06"),
    ("/conversations/{conversation_id}", db.conversations, "C-05", "C-07"),
    ("/plans/{plan_id}", db.learning_plans, "P-04", "P-05"),
    ("/knowledge-bases/{knowledge_base_id}", db.knowledge_bases, "K-05", "K-06"),
    ("/files/{file_id}", db.files, "F-06", "F-07"),
    ("/attachments/{attachment_id}", db.attachments, "I-03"),
]:
    deletion_route(*args)

install(app)
