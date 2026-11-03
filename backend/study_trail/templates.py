"""Versioned administrator templates and isolated personal-copy imports."""
import asyncio
import copy
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import Field, ValidationError, model_validator

from . import admin, db
from . import core as c
from . import platform as p
from . import schemas as s
from .learning_content import D, record_exercise, record_plan

user_router = APIRouter(prefix="/api/v1", tags=["templates"])
admin_router = APIRouter(prefix="/api/admin", tags=["templates-management"])
ASSET_UPLOAD = File(...)

class TemplateCreate(s.Input):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=500)

class VersionAction(s.Input):
    expected_version: int = Field(gt=0, strict=True)

class VersionEdit(VersionAction):
    payload: dict
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)

class ImportTarget(s.Input):
    new_kb_name: str | None = Field(default=None, min_length=1, max_length=50)
    target_kb_id: UUID | None = None
    confirm_reimport: bool = False
    @model_validator(mode="after")
    def one_target(self):
        if (self.new_kb_name is None) == (self.target_kb_id is None):
            raise ValueError("请选择新建或已有知识库")
        if self.new_kb_name is not None and not self.new_kb_name.strip():
            raise ValueError("知识库名称不能为空")
        return self

class Adoption(s.Input):
    version_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=50)
    config_overrides: dict = Field(default_factory=dict)
    import_target: ImportTarget | None = None

class RetryImport(s.Input):
    item_ids: list[UUID] = Field(min_length=1, max_length=100)

def owned_template(conn, ident, actor, lock=False):
    row = c.require(db.one(conn, db.agent_templates, ident, actor, lock=lock))
    if row["status"] == "deleted":
        raise c.Problem("RESOURCE_NOT_FOUND", "模板不存在", 404)
    return row

def owned_version(conn, ident, actor, lock=False):
    # All lifecycle writers lock the template before its version. A preflight
    # read identifies the parent but never authorizes against a stale row.
    row = c.require(db.one(conn, db.template_versions, ident, actor))
    owned_template(conn, row["template_id"], actor, lock)
    return c.require(db.one(conn, db.template_versions, ident, actor, lock=lock)) if lock else row

def version_check(row, expected):
    if row["lock_version"] != expected:
        raise c.Problem("VERSION_CONFLICT", "模板已被修改，请重新载入；草稿已保留", 409)

def draft_only(row):
    if row["status"] != "draft":
        raise c.Problem("VERSION_CONFLICT", "已发布版本不可修改，请创建新草稿", 409)

def asset_dto(row):
    chunks = row.get("chunks") or []
    return {**c.public(row, "id", "template_id", "filename", "media_type", "byte_size", "status", "parse_version", "latest_task_id", "error_code", "created_at", "updated_at"),
        "reference_key": str(row["id"]), "locator": chunks[0]["locator"] if chunks else None}

def template_dto(conn, row):
    versions = conn.execute(sa.select(db.template_versions).where(db.template_versions.c.template_id == row["id"]).order_by(db.template_versions.c.version_no.desc())).mappings()
    return {**c.public(row, "id", "name", "description", "status", "published_version_id", "lock_version", "created_at", "updated_at"),
        "versions": [c.public(x, "id", "version_no", "status", "lock_version", "payload", "created_at") for x in versions]}

def validate_choices(conn, choices, unavailable="TEMPLATE_CONFIG_UNAVAILABLE"):
    if not isinstance(choices, dict) or set(choices) - {"system_prompt", "model_id", "mcp_server_ids", "skill_ids"}:
        raise c.Problem("VALIDATION_ERROR", "模板配置字段不合法", 422)
    if not isinstance(choices.get("system_prompt"), str) or not choices["system_prompt"].strip() or len(choices["system_prompt"]) > 10000:
        raise c.Problem("TEMPLATE_INCOMPLETE", "请填写有效系统提示词", 409)
    model = choices.get("model_id")
    rows = conn.execute(sa.select(db.platform_configs).where(db.platform_configs.c.enabled.is_(True), db.platform_configs.c.deleted_at.is_(None))).mappings().all()
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row["kind"], {})[row["stable_id"]] = row
    entry = by_kind.get("model", {}).get(model)
    if not entry:
        raise c.Problem(unavailable, "模板生成模型已不可用，请明确选择可用替代", 409)
    payload = db.one(conn, db.config_revisions, entry["current_revision_id"])["payload"]
    if payload.get("model_kind") != "generative" or not payload.get("exposed", True):
        raise c.Problem(unavailable, "模板生成模型未开放", 409)
    for key, kind in (("mcp_server_ids", "mcp"), ("skill_ids", "skill")):
        ids = choices.get(key, [])
        if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            raise c.Problem("VALIDATION_ERROR", "能力选择必须是不重复的标识数组", 422)
        if set(ids) - set(by_kind.get(kind, {})):
            raise c.Problem(unavailable, "模板包含失效能力，请明确清理或替换", 409)
    return choices

def validate_template_payload(conn, template, payload, publishing=False):
    if set(payload) - {"config_choices", "plan", "exercises", "asset_ids", "name", "description"}:
        raise c.Problem("VALIDATION_ERROR", "模板正文包含未知字段", 422)
    if "name" in payload and (not isinstance(payload["name"], str) or not 1 <= len(payload["name"].strip()) <= 50):
        raise c.Problem("VALIDATION_ERROR", "模板名称不合法", 422)
    if "description" in payload and (not isinstance(payload["description"], str) or len(payload["description"]) > 500):
        raise c.Problem("VALIDATION_ERROR", "模板说明不合法", 422)
    if not publishing:
        if not isinstance(payload.get("exercises", []), list) or not isinstance(payload.get("asset_ids", []), list):
            raise c.Problem("VALIDATION_ERROR", "模板题目或资料清单格式错误", 422)
        return jsonable_encoder(payload)
    validate_choices(conn, payload.get("config_choices", {}))
    try:
        plan = s.PlanPayload.model_validate(payload.get("plan"))
    except ValidationError:
        raise c.Problem("TEMPLATE_INCOMPLETE", "模板需要完整学习计划", 409) from None
    exercises = payload.get("exercises")
    if not isinstance(exercises, list) or not exercises:
        raise c.Problem("TEMPLATE_INCOMPLETE", "模板至少需要一道练习", 409)
    asset_ids = payload.get("asset_ids", [])
    try:
        assets = [c.require(db.one(conn, db.template_assets, UUID(str(x)), template["owner_id"])) for x in asset_ids]
    except (ValueError, TypeError):
        raise c.Problem("VALIDATION_ERROR", "资料标识不合法", 422) from None
    if not assets or len(set(asset_ids)) != len(asset_ids) or any(x["template_id"] != template["id"] or x["status"] != "ready" for x in assets):
        raise c.Problem("TEMPLATE_INCOMPLETE", "发布资料必须属于当前模板且全部处理成功", 409)
    legal_refs = {str(x["id"]) for x in assets}
    used = {str(x) for st in plan.stages for task in st.tasks for x in task.resource_source_ids}
    stage_tasks = {str(stage.id): {str(task.id) for task in stage.tasks} for stage in plan.stages}
    for ex in exercises:
        if not isinstance(ex, dict) or set(ex) - {"question_text", "answer_text", "explanation", "provenance", "source_ids", "links"}:
            raise c.Problem("VALIDATION_ERROR", "模板习题字段不合法", 422)
        if not isinstance(ex.get("question_text"), str) or not ex["question_text"].strip() or ex.get("provenance", "generated") not in {"original", "adapted", "generated"}:
            raise c.Problem("TEMPLATE_INCOMPLETE", "模板习题内容不完整", 409)
        for field in ("answer_text", "explanation"):
            if ex.get(field) is not None and not isinstance(ex[field], str):
                raise c.Problem("VALIDATION_ERROR", "答案解析必须是文字", 422)
        if not isinstance(ex.get("source_ids", []), list):
            raise c.Problem("VALIDATION_ERROR", "习题来源格式错误", 422)
        links = ex.get("links", [])
        if not isinstance(links, list) or len(links) > 1:
            raise c.Problem("VALIDATION_ERROR", "模板练习最多关联当前计划的一个阶段任务", 422)
        for link in links:
            if not isinstance(link, dict) or set(link) - {"stage_id", "task_id"}:
                raise c.Problem("VALIDATION_ERROR", "模板练习关联字段不合法", 422)
            try:
                stage_id = str(UUID(str(link["stage_id"]))) if link.get("stage_id") else None
                task_id = str(UUID(str(link["task_id"]))) if link.get("task_id") else None
            except (ValueError, TypeError):
                raise c.Problem("VALIDATION_ERROR", "模板练习关联标识不合法", 422) from None
            if stage_id and stage_id not in stage_tasks or task_id and (not stage_id or task_id not in stage_tasks[stage_id]):
                raise c.Problem("TEMPLATE_INCOMPLETE", "模板练习关联不属于当前计划阶段任务", 409)
            link.update(stage_id=stage_id, task_id=task_id)
        used.update(str(x) for x in ex.get("source_ids", []))
    if used - legal_refs:
        raise c.Problem("TEMPLATE_INCOMPLETE", "计划或习题引用了不在发布清单中的资料", 409)
    return {**jsonable_encoder(payload), "plan": plan.model_dump(mode="json")}

def preview(conn, template, version, include_answers=False):
    payload = copy.deepcopy(version["payload"])
    choices = payload.get("config_choices", {})
    try:
        validate_choices(conn, choices)
        capability = {"available": True, "error_code": None}
    except c.Problem as exc:
        capability = {"available": False, "error_code": exc.code, "message": exc.message}
    exercises = payload.get("exercises", [])
    if not include_answers:
        exercises = [{k: v for k, v in ex.items() if k not in {"answer_text", "explanation"}} for ex in exercises]
    manifests = conn.execute(sa.select(db.template_assets, db.template_version_assets.c.manifest).join(db.template_version_assets, db.template_version_assets.c.asset_id == db.template_assets.c.id).where(db.template_version_assets.c.version_id == version["id"]).order_by(db.template_version_assets.c.position)).mappings()
    return {"id": template["id"], "version_id": version["id"], "name": payload.get("name", template["name"]), "description": payload.get("description", template["description"]),
        "config_choices": choices, "plan": payload.get("plan"), "exercises": exercises,
        "asset_manifest": [{"id": a["id"], "filename": a["filename"], "byte_size": a["byte_size"], "reference_key": str(a["id"]), "locator": (a["chunks"] or [{}])[0].get("locator")} for a in manifests], "capability_status": capability}

@admin_router.get("/agent-templates", operation_id="AD-LT01")
def admin_templates(request: Request, ctx=admin.D):
    page = admin.paginate(ctx[0], db.agent_templates, request, ctx[1]["id"], [db.agent_templates.c.owner_id == ctx[1]["id"], db.agent_templates.c.status != "deleted"])
    page["items"] = [template_dto(ctx[0], x) for x in page["items"]]
    return c.envelope(page)

@admin_router.post("/agent-templates", operation_id="AD-LT02")
def create_template(body: TemplateCreate, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        if not body.name.strip():
            raise c.Problem("VALIDATION_ERROR", "名称不能为空", 422)
        row = db.insert(conn, db.agent_templates, owner_id=user["id"], admin_id=user["id"], name=body.name.strip(), description=body.description)
        db.insert(conn, db.template_versions, owner_id=user["id"], template_id=row["id"], version_no=1, payload={"config_choices": {}, "plan": None, "exercises": [], "asset_ids": []})
        return template_dto(conn, row), 201
    return admin.write(conn, user, request, body.model_dump(), action, "template")

@admin_router.get("/agent-templates/{id}", operation_id="AD-LT03")
def template_detail(id: UUID, ctx=admin.D):
    return c.envelope(template_dto(ctx[0], owned_template(ctx[0], id, ctx[1]["id"])))

@admin_router.post("/agent-templates/{id}/drafts", operation_id="AD-LT04")
def new_draft(id: UUID, body: VersionAction, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        template = owned_template(conn, id, user["id"], True)
        version_check(template, body.expected_version)
        versions = conn.execute(sa.select(db.template_versions).where(db.template_versions.c.template_id == id).order_by(db.template_versions.c.version_no.desc())).mappings().all()
        if any(x["status"] == "draft" for x in versions):
            raise c.Problem("VERSION_CONFLICT", "请先编辑现有草稿", 409)
        source = db.one(conn, db.template_versions, template["published_version_id"]) if template["published_version_id"] else versions[0]
        row = db.insert(conn, db.template_versions, owner_id=user["id"], template_id=id, version_no=versions[0]["version_no"] + 1, payload=source["payload"])
        for link in conn.execute(sa.select(db.template_version_assets).where(db.template_version_assets.c.version_id == source["id"])).mappings().all():
            db.insert(conn, db.template_version_assets, owner_id=user["id"], version_id=row["id"], asset_id=link["asset_id"], position=link["position"], manifest=link["manifest"])
        db.update(conn, db.agent_templates, id, lock_version=template["lock_version"] + 1)
        return c.public(row, "id", "template_id", "version_no", "status", "lock_version", "payload"), 201
    return admin.write(conn, user, request, body.model_dump(), action, "template", id)

@admin_router.put("/template-versions/{id}", operation_id="AD-LT05")
def save_draft(id: UUID, body: VersionEdit, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        row = owned_version(conn, id, user["id"], True)
        draft_only(row)
        version_check(row, body.expected_version)
        template = owned_template(conn, row["template_id"], user["id"], True)
        proposed = {**body.payload, "name": body.name if body.name is not None else body.payload.get("name", row["payload"].get("name", template["name"])),
            "description": body.description if body.description is not None else body.payload.get("description", row["payload"].get("description", template["description"]))}
        payload = validate_template_payload(conn, template, proposed)
        try:
            ids = [UUID(str(x)) for x in payload.get("asset_ids", [])]
        except (ValueError, TypeError):
            raise c.Problem("VALIDATION_ERROR", "资料标识不合法", 422) from None
        if len(set(ids)) != len(ids):
            raise c.Problem("VALIDATION_ERROR", "资料不能重复", 422)
        for asset in ids:
            a = c.require(db.one(conn, db.template_assets, asset, user["id"]))
            if a["template_id"] != template["id"]:
                raise c.Problem("RESOURCE_NOT_FOUND", "资料不属于此模板", 404)
        conn.execute(db.template_version_assets.delete().where(db.template_version_assets.c.version_id == id))
        for i, asset in enumerate(ids):
            db.insert(conn, db.template_version_assets, owner_id=user["id"], version_id=id, asset_id=asset, position=i)
        row = db.update(conn, db.template_versions, id, payload=payload, lock_version=row["lock_version"] + 1)
        return c.public(row, "id", "template_id", "version_no", "status", "lock_version", "payload"), 200
    return admin.write(conn, user, request, body.model_dump(), action, "template_version", id)

@admin_router.post("/template-versions/{id}/publish", operation_id="AD-LT06")
def publish_version(id: UUID, body: VersionAction, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        p.lock(conn)
        row = owned_version(conn, id, user["id"], True)
        draft_only(row)
        version_check(row, body.expected_version)
        template = owned_template(conn, row["template_id"], user["id"], True)
        payload = validate_template_payload(conn, template, row["payload"], True)
        for manifest in conn.execute(sa.select(db.template_version_assets).where(db.template_version_assets.c.version_id == id)).mappings().all():
            asset = db.one(conn, db.template_assets, manifest["asset_id"])
            conn.execute(db.template_version_assets.update().where(db.template_version_assets.c.id == manifest["id"]).values(manifest={"sha256": asset["sha256"].hex(), "filename": asset["filename"], "parse_version": asset["parse_version"], "chunks": asset["chunks"]}))
        db.update(conn, db.template_versions, id, payload=payload, status="published", lock_version=row["lock_version"] + 1)
        template = db.update(conn, db.agent_templates, template["id"], published_version_id=id, status="published", lock_version=template["lock_version"] + 1,
            name=payload.get("name", template["name"]).strip(), description=payload.get("description", template["description"]))
        return template_dto(conn, template), 200
    return admin.write(conn, user, request, body.model_dump(), action, "template_version", id)

def set_template_status(id, body, request, ctx, status):
    conn, user, _ = ctx
    def action():
        row = owned_template(conn, id, user["id"], True)
        version_check(row, body.expected_version)
        row = db.update(conn, db.agent_templates, id, status=status, lock_version=row["lock_version"] + 1)
        return ({"deleted_id": id} if status == "deleted" else template_dto(conn, row)), 200
    return admin.write(conn, user, request, body.model_dump(), action, "template", id)

@admin_router.post("/agent-templates/{id}/offline", operation_id="AD-LT07")
def offline(id: UUID, body: VersionAction, request: Request, ctx=admin.D):
    return set_template_status(id, body, request, ctx, "offline")

@admin_router.delete("/agent-templates/{id}", operation_id="AD-LT08")
def delete_template(id: UUID, body: VersionAction, request: Request, ctx=admin.D):
    return set_template_status(id, body, request, ctx, "deleted")

@admin_router.get("/agent-templates/{id}/assets", operation_id="AD-LT09")
def assets(id: UUID, request: Request, ctx=admin.D):
    owned_template(ctx[0], id, ctx[1]["id"])
    page = admin.paginate(ctx[0], db.template_assets, request, ctx[1]["id"], [db.template_assets.c.template_id == id, db.template_assets.c.owner_id == ctx[1]["id"]])
    page["items"] = [asset_dto(x) for x in page["items"]]
    return c.envelope(page)

@admin_router.post("/agent-templates/{id}/assets", operation_id="AD-LT10")
async def upload_assets(id: UUID, request: Request, files: list[UploadFile] = ASSET_UPLOAD):
    with db.engine.begin() as conn:
        user, _ = c.authenticate(conn, request, realm="admin")
        owned_template(conn, id, user["id"])
        limits = p.limits(conn)
    if not 1 <= len(files) <= limits["batch_max_files"]:
        raise c.Problem("VALIDATION_ERROR", "文件数量超过限制", 422)
    entries = []
    for file in files:
        data = await file.read(limits["file_max_bytes"] + 1)
        ext = Path(file.filename or "").suffix.lower()
        invalid = not data or len(data) > limits["file_max_bytes"] or ext not in {".txt", ".pdf", ".docx"}
        invalid = invalid or ext == ".pdf" and not data.lstrip().startswith(b"%PDF-") or ext == ".docx" and not data.startswith(b"PK")
        entries.append((Path(file.filename or "文件").name[:255], ext, data, invalid))
    written = []
    try:
        with db.engine.begin() as conn:
            user, _ = c.authenticate(conn, request, realm="admin")
            def action():
                owned_template(conn, id, user["id"], True)
                result = []
                for name, ext, data, invalid in entries:
                    if invalid:
                        result.append({"original_name": name, "error": {"code": "VALIDATION_ERROR", "message": "文件为空、过大或格式不支持"}})
                        continue
                    key = c.store_bytes(data)
                    written.append(key)
                    asset = db.insert(conn, db.template_assets, owner_id=user["id"], template_id=id, filename=name,
                        extension=ext, media_type={".txt": "text/plain", ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[ext],
                        byte_size=len(data), storage_key=key, sha256=hashlib.sha256(data).digest())
                    task = db.insert(conn, db.tasks, owner_id=user["id"], kind="template_asset_process", input_snapshot={"asset_id": str(asset["id"])})
                    asset = db.update(conn, db.template_assets, asset["id"], latest_task_id=task["id"])
                    result.append({"asset": asset_dto(asset), "task": {"id": task["id"], "status": task["status"]}})
                return {"items": result}, 202
            return admin.write(conn, user, request, [{"name": n, "sha256": hashlib.sha256(d).hexdigest()} for n, _, d, _ in entries], action, "template", id)
    except Exception:
        for key in written:
            c.storage_path(key).unlink(missing_ok=True)
        raise

@admin_router.get("/template-assets/{id}/preview", operation_id="AD-LT11")
def asset_preview(id: UUID, cursor: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), ctx=admin.D):
    row = c.require(db.one(ctx[0], db.template_assets, id, ctx[1]["id"]))
    if row["status"] != "ready":
        raise c.Problem("FILE_NOT_READY", "资料尚未处理成功", 409)
    parts = row["chunks"][cursor:cursor + limit]
    return c.envelope({"id": id, "name": row["filename"], "reference_key": str(id), "blocks": [{"id": x["chunk_index"], "text": x["content_text"], "locator": x["locator"]} for x in parts],
        "next_cursor": cursor + limit if cursor + limit < len(row["chunks"]) else None, "warning": "仅提取文字，不还原排版。"})

@admin_router.post("/template-assets/{id}/retry", operation_id="AD-LT12")
def retry_asset(id: UUID, request: Request, body: s.Empty, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        asset = c.require(db.one(conn, db.template_assets, id, user["id"], lock=True))
        owned_template(conn, asset["template_id"], user["id"])
        if asset["status"] != "failed":
            raise c.Problem("VERSION_CONFLICT", "只有失败资料可重试", 409)
        task = db.insert(conn, db.tasks, owner_id=user["id"], kind="template_asset_process", input_snapshot={"asset_id": str(id)})
        asset = db.update(conn, db.template_assets, id, status="processing", error_code=None, latest_task_id=task["id"])
        return {"asset": asset_dto(asset), "task": {"id": task["id"], "status": task["status"]}}, 202
    return admin.write(conn, user, request, {}, action, "template_asset", id)

@admin_router.delete("/template-versions/{id}/assets/{asset_id}", operation_id="AD-LT13")
def unlink_asset(id: UUID, asset_id: UUID, body: VersionAction, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        row = owned_version(conn, id, user["id"], True)
        draft_only(row)
        version_check(row, body.expected_version)
        asset = c.require(db.one(conn, db.template_assets, asset_id, user["id"]))
        if asset["template_id"] != row["template_id"]:
            raise c.Problem("RESOURCE_NOT_FOUND", "资料不存在", 404)
        conn.execute(db.template_version_assets.delete().where(db.template_version_assets.c.version_id == id, db.template_version_assets.c.asset_id == asset_id))
        payload = {**row["payload"], "asset_ids": [x for x in row["payload"].get("asset_ids", []) if x != str(asset_id)]}
        db.update(conn, db.template_versions, id, payload=payload, lock_version=row["lock_version"] + 1)
        return {"deleted_id": asset_id}, 200
    return admin.write(conn, user, request, body.model_dump(), action, "template_version", id)

@user_router.get("/agent-templates", operation_id="LT-01")
def directory(request: Request, ctx=D):
    page = admin.paginate(ctx[0], db.agent_templates, request, ctx[1]["id"], [db.agent_templates.c.status == "published"])
    page["items"] = [{**c.public(x, "id", "name", "description", "published_version_id"), "version_id": x["published_version_id"]} for x in page["items"]]
    return c.envelope(page)

@user_router.get("/agent-templates/{id}/versions/{version_id}", operation_id="LT-02")
def user_preview(id: UUID, version_id: UUID, include_answers: bool = False, ctx=D):
    template = c.require(db.one(ctx[0], db.agent_templates, id))
    if template["status"] != "published" or template["published_version_id"] != version_id:
        raise c.Problem("TEMPLATE_NOT_PUBLISHED", "该模板版本当前未上架", 409)
    return c.envelope(preview(ctx[0], template, c.require(db.one(ctx[0], db.template_versions, version_id)), include_answers))

def replace_refs(value, mappings):
    if isinstance(value, list):
        return [replace_refs(x, mappings) for x in value]
    if isinstance(value, dict):
        return {k: replace_refs(v, mappings) for k, v in value.items()}
    return mappings.get(value, value) if isinstance(value, str) else value

def adoption_dto(conn, row):
    batch = conn.execute(sa.select(db.template_import_batches).where(db.template_import_batches.c.adoption_id == row["id"]).order_by(db.template_import_batches.c.created_at.desc()).limit(1)).mappings().first()
    return {"id": row["id"], "agent_id": row["agent_id"], "template_version_id": row["template_version_id"],
        "plan_ids": list(conn.scalars(sa.select(db.learning_plans.c.id).where(db.learning_plans.c.adoption_id == row["id"]))),
        "exercise_ids": list(conn.scalars(sa.select(db.exercises.c.id).where(db.exercises.c.adoption_id == row["id"]))),
        "import_batch_id": batch["id"] if batch else None, "import_status": batch["status"] if batch else "not_imported"}

def start_import(conn, adoption, target):
    owner = adoption["owner_id"]
    if target.new_kb_name:
        kb = db.insert(conn, db.knowledge_bases, owner_id=owner, name=target.new_kb_name.strip())
    else:
        kb = c.require(db.one(conn, db.knowledge_bases, target.target_kb_id, owner, lock=True))
    old_items = conn.execute(sa.select(db.template_import_items).where(db.template_import_items.c.adoption_id == adoption["id"], db.template_import_items.c.target_kb_id == kb["id"])).mappings().all()
    if old_items and not target.confirm_reimport:
        deleted = any(x["status"] == "deleted" or x["file_id"] is None and x["status"] != "waiting" for x in old_items)
        if deleted:
            raise c.Problem("IMPORT_ITEM_DELETED", "个人文件已删除，请明确确认重新导入", 409)
        return db.one(conn, db.template_import_batches, old_items[0]["batch_id"])
    if old_items and any(x["status"] in {"waiting", "processing"} for x in old_items):
        raise c.Problem("VERSION_CONFLICT", "已有导入仍在执行", 409)
    batch = db.insert(conn, db.template_import_batches, owner_id=owner, adoption_id=adoption["id"], target_kb_id=kb["id"])
    assets = list(conn.scalars(sa.select(db.template_version_assets.c.asset_id).where(db.template_version_assets.c.version_id == adoption["template_version_id"]).order_by(db.template_version_assets.c.position)))
    by_asset = {x["asset_id"]: x for x in old_items}
    for asset in assets:
        previous = by_asset.get(asset)
        if previous:
            # User-confirmed reimport is a new operation; successful files remain.
            status = "succeeded" if previous["file_id"] and previous["status"] == "succeeded" else "waiting"
            db.update(conn, db.template_import_items, previous["id"], batch_id=batch["id"], status=status, error_code=None, attempt_no=previous["attempt_no"] + 1)
        else:
            db.insert(conn, db.template_import_items, owner_id=owner, batch_id=batch["id"], adoption_id=adoption["id"], asset_id=asset, target_kb_id=kb["id"])
    db.insert(conn, db.tasks, owner_id=owner, kind="template_import", input_snapshot={"batch_id": str(batch["id"])})
    return batch

@user_router.post("/template-adoptions", operation_id="LT-03")
def adopt(body: Adoption, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        p.lock(conn)
        version = c.require(db.one(conn, db.template_versions, body.version_id))
        template = c.require(db.one(conn, db.agent_templates, version["template_id"], lock=True))
        if template["status"] != "published" or template["published_version_id"] != version["id"]:
            raise c.Problem("TEMPLATE_NOT_PUBLISHED", "该版本当前未上架", 409)
        if set(body.config_overrides) - {"model_id", "mcp_server_ids", "skill_ids"}:
            raise c.Problem("VALIDATION_ERROR", "替代配置字段不合法", 422)
        payload = copy.deepcopy(version["payload"])
        choices = {**payload["config_choices"], **body.config_overrides}
        validate_choices(conn, choices)
        agent = db.insert(conn, db.agents, owner_id=user["id"], name=(body.name or template["name"]).strip(), description=template["description"],
            system_prompt=choices["system_prompt"], model_id=choices["model_id"], mcp_server_ids=choices.get("mcp_server_ids", []), skill_ids=choices.get("skill_ids", []))
        adoption = db.insert(conn, db.template_adoptions, owner_id=user["id"], agent_id=agent["id"], template_version_id=version["id"])
        all_refs = {str(x) for st in payload["plan"]["stages"] for t in st["tasks"] for x in t["resource_source_ids"]}
        all_refs.update(str(x) for ex in payload["exercises"] for x in ex.get("source_ids", []))
        mappings = {}
        for key in sorted(all_refs):
            asset = c.require(db.one(conn, db.template_assets, UUID(key)))
            source = db.insert(conn, db.sources, owner_id=user["id"], kind="template_placeholder", title_snapshot=asset["filename"], excerpt_snapshot="",
                locator_snapshot=(asset["chunks"] or [{}])[0].get("locator"), adoption_id=adoption["id"], reference_key=key)
            db.insert(conn, db.template_source_mappings, owner_id=user["id"], adoption_id=adoption["id"], reference_key=key, source_id=source["id"], asset_id=asset["id"])
            mappings[key] = str(source["id"])
        plan_payload = replace_refs(payload["plan"], mappings)
        # Personal copies receive independent stage/task identities.
        node_ids = {x["id"]: str(uuid4()) for x in plan_payload["stages"]}
        node_ids.update({t["id"]: str(uuid4()) for x in plan_payload["stages"] for t in x["tasks"]})
        plan_payload = replace_refs(plan_payload, node_ids)
        plan = db.insert(conn, db.learning_plans, owner_id=user["id"], agent_id=agent["id"], name=plan_payload["title"],
            content={k: v for k, v in plan_payload.items() if k != "title"}, adoption_id=adoption["id"], origin_kind="template")
        record_plan(conn, plan, "template")
        used_plan = {x for st in plan_payload["stages"] for t in st["tasks"] for x in t["resource_source_ids"]}
        for i, ident in enumerate(sorted(used_plan)):
            conn.execute(db.plan_sources.insert().values(owner_id=user["id"], plan_id=plan["id"], source_id=UUID(ident), position=i, content_version=1))
        for i, ex in enumerate(payload["exercises"]):
            ex = replace_refs(ex, {**mappings, **node_ids})
            hidden = {"answer_text": ex.get("answer_text"), "explanation": ex.get("explanation"), "source_ids": ex.get("source_ids", [])} if ex.get("answer_text") or ex.get("explanation") else None
            row = db.insert(conn, db.exercises, owner_id=user["id"], agent_id=agent["id"], position=i, question_text=ex["question_text"],
                provenance=ex.get("provenance", "generated"), source_ids=ex.get("source_ids", []), known_answer_snapshot=hidden, origin_kind="template", adoption_id=adoption["id"])
            record_exercise(conn, row, "template")
            for link in ex.get("links", []):
                stage = link.get("stage_id")
                task_id = link.get("task_id")
                if stage and stage not in node_ids.values() or task_id and task_id not in node_ids.values():
                    raise c.Problem("TEMPLATE_INCOMPLETE", "模板练习关联已失效", 409)
                db.insert(conn, db.exercise_plan_links, owner_id=user["id"], exercise_id=row["id"], plan_id=plan["id"], stage_id=UUID(stage) if stage else None, task_id=UUID(task_id) if task_id else None)
        if body.import_target:
            start_import(conn, adoption, body.import_target)
        return adoption_dto(conn, adoption), 201, [["agents", str(agent["id"])], ["template_adoptions", str(adoption["id"])]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

@user_router.get("/template-adoptions/{id}", operation_id="LT-04")
def adoption_detail(id: UUID, ctx=D):
    return c.envelope(adoption_dto(ctx[0], c.require(db.one(ctx[0], db.template_adoptions, id, ctx[1]["id"]))))

@user_router.post("/template-adoptions/{id}/imports", operation_id="LT-05")
def import_assets(id: UUID, body: ImportTarget, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        adoption = c.require(db.one(conn, db.template_adoptions, id, user["id"], lock=True))
        batch = start_import(conn, adoption, body)
        return batch_dto(conn, batch), 202, [["template_import_batches", str(batch["id"])]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

def batch_dto(conn, batch):
    rows = conn.execute(sa.select(db.template_import_items).where(db.template_import_items.c.batch_id == batch["id"]).order_by(db.template_import_items.c.created_at, db.template_import_items.c.id)).mappings().all()
    projected = []
    for x in rows:
        item = c.public(x, "id", "asset_id", "file_id", "status", "error_code")
        if x["file_id"] is None and x["status"] in {"succeeded", "processing", "deleted"}:
            item.update(status="deleted", error_code="IMPORT_ITEM_DELETED")
        projected.append({**item, "can_retry": item["status"] == "failed"})
    out = {**c.public(batch, "id", "adoption_id", "target_kb_id", "status", "revision", "error_code"), "items": projected}
    if not batch["target_kb_id"]:
        out.update(status="failed", error_code="IMPORT_TARGET_DELETED")
    elif any(x["status"] == "deleted" for x in projected):
        out["status"] = "partial_failed" if any(x["status"] == "succeeded" for x in projected) else "failed"
    return out

@user_router.get("/template-imports/{id}", operation_id="LT-06")
def import_detail(id: UUID, ctx=D):
    return c.envelope(batch_dto(ctx[0], c.require(db.one(ctx[0], db.template_import_batches, id, ctx[1]["id"]))))

@user_router.post("/template-imports/{id}/retry", operation_id="LT-07")
def retry_import(id: UUID, body: RetryImport, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        batch = c.require(db.one(conn, db.template_import_batches, id, user["id"], lock=True))
        if not batch["target_kb_id"] or not db.one(conn, db.knowledge_bases, batch["target_kb_id"], user["id"]):
            raise c.Problem("IMPORT_TARGET_DELETED", "导入目标已删除，请明确选择新目标", 409)
        if len(set(body.item_ids)) != len(body.item_ids):
            raise c.Problem("VALIDATION_ERROR", "重试项不能重复", 422)
        for ident in body.item_ids:
            item = c.require(db.one(conn, db.template_import_items, ident, user["id"], lock=True))
            if item["batch_id"] != id:
                raise c.Problem("RESOURCE_NOT_FOUND", "导入项不存在", 404)
            if item["status"] == "deleted" or item["file_id"] is None and item["status"] in {"succeeded", "processing"}:
                raise c.Problem("IMPORT_ITEM_DELETED", "个人文件已删除，需明确重新导入", 409)
            if item["status"] != "failed":
                raise c.Problem("VERSION_CONFLICT", "仅失败项可以重试", 409)
            db.update(conn, db.template_import_items, ident, status="waiting", error_code=None, attempt_no=item["attempt_no"] + 1)
        db.update(conn, db.template_import_batches, id, status="processing", revision=batch["revision"] + 1, error_code=None)
        db.insert(conn, db.tasks, owner_id=user["id"], kind="template_import", input_snapshot={"batch_id": str(id), "item_ids": jsonable_encoder(body.item_ids)})
        return batch_dto(conn, batch), 202, [["template_import_batches", str(id)]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

async def process_asset(task):
    from .execution import assert_claim, publish, terminal
    from .files import chunks, extract
    ident = UUID(task["input_snapshot"]["asset_id"])
    with db.engine.begin() as conn:
        assert_claim(conn, task)
        asset = c.require(db.one(conn, db.template_assets, ident, task["owner_id"]))
        path = c.storage_path(asset["storage_key"])
        extension = asset["extension"]
    publish(task, "parse")
    text, metadata = await asyncio.to_thread(extract, path, extension)
    blocks = chunks(text, metadata)
    with db.engine.begin() as conn:
        live = assert_claim(conn, task)
        asset = c.require(db.one(conn, db.template_assets, ident, task["owner_id"], lock=True))
        if asset["latest_task_id"] != task["id"]:
            raise c.Problem("VERSION_CONFLICT", "资料处理尝试已过期", 409)
        db.update(conn, db.template_assets, ident, status="ready", parsed_content=text, text_metadata=metadata, chunks=blocks, error_code=None)
        terminal(conn, live, "succeeded", result={"asset_id": str(ident)})

async def process_import(task):
    from .execution import assert_claim, terminal
    batch_id = UUID(task["input_snapshot"]["batch_id"])
    with db.engine.begin() as conn:
        assert_claim(conn, task)
        batch = c.require(db.one(conn, db.template_import_batches, batch_id, task["owner_id"], lock=True))
        items = conn.execute(sa.select(db.template_import_items).where(db.template_import_items.c.batch_id == batch_id, db.template_import_items.c.status == "waiting")).mappings().all()
    for item in items:
        key = None
        try:
            with db.engine.begin() as conn:
                assert_claim(conn, task)
                asset = c.require(db.one(conn, db.template_assets, item["asset_id"]))
                data_path = c.storage_path(asset["storage_key"])
            data = await asyncio.to_thread(data_path.read_bytes)
            if hashlib.sha256(data).digest() != asset["sha256"]:
                raise c.Problem("SOURCE_UNAVAILABLE", "发布资料校验失败", 409)
            with db.engine.begin() as conn:
                p.lock(conn)
                assert_claim(conn, task)
                batch = c.require(db.one(conn, db.template_import_batches, batch_id, task["owner_id"], lock=True))
                current = c.require(db.one(conn, db.template_import_items, item["id"], task["owner_id"], lock=True))
                if not batch["target_kb_id"] or not db.one(conn, db.knowledge_bases, batch["target_kb_id"], task["owner_id"]):
                    raise c.Problem("IMPORT_TARGET_DELETED", "导入目标已删除", 409)
                if current["status"] != "waiting":
                    continue
                file = db.one(conn, db.files, current["file_id"], task["owner_id"], lock=True) if current["file_id"] else None
                if not file:
                    key = c.store_bytes(data)
                    file = db.insert(conn, db.files, owner_id=task["owner_id"], knowledge_base_id=batch["target_kb_id"], original_name=asset["filename"],
                        extension=asset["extension"], media_type=asset["media_type"], storage_key=key, sha256=asset["sha256"], byte_size=asset["byte_size"])
                else:
                    db.update(conn, db.files, file["id"], status="processing", error_code=None, error_message=None)
                child = db.insert(conn, db.tasks, owner_id=task["owner_id"], kind="file_process", file_id=file["id"], input_snapshot={"file_id": str(file["id"])})
                db.update(conn, db.template_import_items, item["id"], status="processing", file_id=file["id"], task_id=child["id"], error_code=None)
                key = None
        except (OSError, c.Problem, sa.exc.SQLAlchemyError, ValueError) as exc:
            if key:
                c.storage_path(key).unlink(missing_ok=True)
            with db.engine.begin() as conn:
                assert_claim(conn, task)
                db.update(conn, db.template_import_items, item["id"], status="failed", error_code=getattr(exc, "code", "IMPORT_FAILED"))
    with db.engine.begin() as conn:
        live = assert_claim(conn, task)
        refresh_imports(conn, batch_id=batch_id)
        terminal(conn, live, "succeeded", result={"batch_id": str(batch_id)})

def promote_source(conn, mapping, file):
    """Enable only current personal content; old message references remain frozen."""
    file = c.require(db.one(conn, db.files, file["id"], mapping["owner_id"], lock=True))
    mapping = c.require(db.one(conn, db.template_source_mappings, mapping["id"], mapping["owner_id"], lock=True))
    if file["status"] != "ready":
        return
    old = db.one(conn, db.sources, mapping["source_id"], mapping["owner_id"])
    if mapping["status"] == "available" and mapping["file_id"] == file["id"]:
        return
    locator = old["locator_snapshot"] or {}
    chunks = conn.execute(sa.select(db.document_chunks).where(db.document_chunks.c.file_id == file["id"], db.document_chunks.c.owner_id == mapping["owner_id"]).order_by(db.document_chunks.c.chunk_index)).mappings().all()
    chunk = next((x for x in chunks if all(x["locator"].get(k) == v for k, v in locator.items())), None)
    source = db.insert(conn, db.sources, owner_id=mapping["owner_id"], kind="file", file_id=file["id"], original_file_id=file["id"],
        chunk_id=chunk["id"] if chunk else None, title_snapshot=file["original_name"], excerpt_snapshot=chunk["content_text"] if chunk else "",
        locator_snapshot=chunk["locator"] if chunk else None, adoption_id=mapping["adoption_id"], reference_key=mapping["reference_key"])
    refs = {str(old["id"]): str(source["id"])}
    plan_ids = list(conn.scalars(sa.select(db.learning_plans.c.id).where(db.learning_plans.c.adoption_id == mapping["adoption_id"])))
    exercise_ids = list(conn.scalars(sa.select(db.exercises.c.id).where(db.exercises.c.adoption_id == mapping["adoption_id"])))
    locked = {}
    for ident, table in sorted([(x, db.learning_plans) for x in plan_ids] + [(x, db.exercises) for x in exercise_ids], key=lambda x: str(x[0])):
        current = db.one(conn, table, ident, mapping["owner_id"], lock=True)
        if current:
            locked[ident] = current
    for ident in plan_ids:
        plan = locked.get(ident)
        if not plan:
            continue
        content = replace_refs(plan["content"], refs)
        if content != plan["content"]:
            plan = db.update(conn, db.learning_plans, plan["id"], content=content, content_version=plan["content_version"] + 1)
            conn.execute(db.plan_sources.update().where(db.plan_sources.c.plan_id == plan["id"], db.plan_sources.c.source_id == old["id"]).values(source_id=source["id"], content_version=plan["content_version"]))
            record_plan(conn, plan, plan["origin_kind"])
    for ident in exercise_ids:
        exercise = locked.get(ident)
        if not exercise:
            continue
        ids = replace_refs(exercise["source_ids"], refs)
        hidden = replace_refs(exercise["known_answer_snapshot"], refs)
        if ids != exercise["source_ids"] or hidden != exercise["known_answer_snapshot"]:
            exercise = db.update(conn, db.exercises, exercise["id"], source_ids=ids, known_answer_snapshot=hidden, content_version=exercise["content_version"] + 1)
            record_exercise(conn, exercise, exercise["origin_kind"])
    db.update(conn, db.template_source_mappings, mapping["id"], status="available", file_id=file["id"], source_id=source["id"])

def refresh_imports(conn, file_id=None, batch_id=None):
    query = sa.select(db.template_import_items)
    if file_id:
        query = query.where(db.template_import_items.c.file_id == file_id)
    elif batch_id:
        query = query.where(db.template_import_items.c.batch_id == batch_id)
    else:
        return
    items = conn.execute(query).mappings().all()
    batches = {x["batch_id"] for x in items}
    for item in items:
        file = db.one(conn, db.files, item["file_id"], item["owner_id"]) if item["file_id"] else None
        if file and file["status"] == "ready":
            status, error = "succeeded", None
            for mapping in conn.execute(sa.select(db.template_source_mappings).where(db.template_source_mappings.c.adoption_id == item["adoption_id"], db.template_source_mappings.c.asset_id == item["asset_id"])).mappings().all():
                promote_source(conn, mapping, file)
        elif file and file["status"] == "failed":
            status, error = "failed", file["error_code"]
        elif not file and item["status"] in {"succeeded", "processing", "deleted"}:
            status, error = "deleted", "IMPORT_ITEM_DELETED"
        else:
            continue
        if status != item["status"] or error != item["error_code"]:
            db.update(conn, db.template_import_items, item["id"], status=status, error_code=error)
    for ident in batches:
        batch = db.one(conn, db.template_import_batches, ident, lock=True)
        items = conn.execute(sa.select(db.template_import_items).where(db.template_import_items.c.batch_id == ident)).mappings().all()
        statuses = {x["status"] for x in items}
        kb = db.one(conn, db.knowledge_bases, batch["target_kb_id"], batch["owner_id"]) if batch["target_kb_id"] else None
        error = None
        if not kb:
            status, error = "failed", "IMPORT_TARGET_DELETED"
        elif items and statuses == {"succeeded"}:
            status = "completed"
            adoption = db.one(conn, db.template_adoptions, batch["adoption_id"])
            db.insert_once(conn, db.agent_knowledge_bases, {"agent_id": adoption["agent_id"], "knowledge_base_id": kb["id"], "owner_id": batch["owner_id"]}, ["agent_id", "knowledge_base_id"])
        elif statuses.intersection({"waiting", "processing"}):
            status = "processing"
        else:
            status = "partial_failed" if "succeeded" in statuses else "failed"
        if batch["status"] != status or batch["error_code"] != error:
            db.update(conn, db.template_import_batches, ident, status=status, error_code=error, revision=batch["revision"] + 1)


def fail_import(conn, task, code):
    """A lost coordinator must leave its unstarted files explicitly retryable."""
    value = task["input_snapshot"].get("batch_id")
    if not value:
        return
    batch_id = UUID(str(value))
    batch = db.one(conn, db.template_import_batches, batch_id, task["owner_id"], lock=True)
    if not batch:
        return
    query = db.template_import_items.update().where(
        db.template_import_items.c.batch_id == batch_id,
        db.template_import_items.c.owner_id == task["owner_id"],
        db.template_import_items.c.status == "waiting")
    item_ids = task["input_snapshot"].get("item_ids")
    if item_ids:
        query = query.where(db.template_import_items.c.id.in_([UUID(str(x)) for x in item_ids]))
    conn.execute(query.values(status="failed", error_code=code))
    refresh_imports(conn, batch_id=batch_id)

def register(app):
    app.include_router(user_router)
    app.include_router(admin_router)
