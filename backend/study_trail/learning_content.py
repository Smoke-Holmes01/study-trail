"""Independent exercises, explicit plan editing and immutable historical revisions."""
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field, model_validator

from . import core as c
from . import db
from . import schemas as s

router = APIRouter(prefix="/api/v1", tags=["learning-content"])

def context(request: Request):
    with db.engine.begin() as conn:
        user, session = c.authenticate(conn, request)
        yield conn, user, session
D = Depends(context, scope="function")

def record_plan(conn, plan, origin_kind="generated", editor_id=None):
    old = conn.scalar(sa.select(db.learning_plan_revisions.c.id).where(db.learning_plan_revisions.c.plan_id == plan["id"], db.learning_plan_revisions.c.content_version == plan["content_version"]))
    if not old:
        db.insert(conn, db.learning_plan_revisions, owner_id=plan["owner_id"], plan_id=plan["id"],
            content_version=plan["content_version"], name_snapshot=plan["name"], payload=plan["content"], origin_kind=origin_kind, editor_id=editor_id)
    return db.one(conn, db.learning_plans, plan["id"])

def record_exercise(conn, exercise, origin_kind="generated", editor_id=None, payload=None):
    version = exercise.get("content_version", 1)
    old = conn.execute(sa.select(db.exercise_revisions).where(db.exercise_revisions.c.exercise_id == exercise["id"], db.exercise_revisions.c.version == version)).mappings().first()
    if not old:
        hidden = exercise.get("known_answer_snapshot") or {}
        payload = payload or {"question_text": exercise["question_text"], "answer_text": hidden.get("answer_text"),
            "explanation": hidden.get("explanation"), "source_ids": exercise["source_ids"], "provenance": exercise["provenance"],
            "answer_source_ids": hidden.get("source_ids", exercise["source_ids"]) if hidden else []}
        payload = dict(payload)
        payload.setdefault("answer_source_ids", hidden.get("source_ids", payload.get("source_ids", []))
                           if payload.get("answer_text") or payload.get("explanation") else [])
        payload.setdefault("answer_origin_kind", hidden.get("origin_kind") or
                           ("original" if payload["answer_source_ids"] and origin_kind == "generated" else origin_kind))
        old = db.insert(conn, db.exercise_revisions, owner_id=exercise["owner_id"], exercise_id=exercise["id"], version=version,
            payload=jsonable_encoder(payload), origin_kind=origin_kind, editor_id=editor_id)
        used_sources = {str(x) for x in [*payload.get("source_ids", []), *payload["answer_source_ids"]]}
        for source in sorted(used_sources):
            db.insert(conn, db.exercise_sources, owner_id=exercise["owner_id"], revision_id=old["id"], source_id=UUID(str(source)))
    return db.update(conn, db.exercises, exercise["id"], current_revision_id=old["id"])

def exercise_dto(conn, row):
    out = {**c.exercise(row), **c.public(row, "agent_id", "current_revision_id", "content_version", "origin_kind", "user_edited", "adoption_id", "created_at", "updated_at")}
    out["links"] = [c.public(x, "plan_id", "stage_id", "task_id") for x in conn.execute(sa.select(db.exercise_plan_links).where(db.exercise_plan_links.c.exercise_id == row["id"])).mappings()]
    out["sources"] = [c.source(x) for x in conn.execute(sa.select(db.sources).where(db.sources.c.owner_id == row["owner_id"], db.sources.c.id.in_([UUID(str(x)) for x in row["source_ids"]]))).mappings()]
    return out

def revision_payload(conn, row, version=None):
    revision = conn.execute(sa.select(db.exercise_revisions).where(db.exercise_revisions.c.exercise_id == row["id"], db.exercise_revisions.c.version == (version or row["content_version"]))).mappings().first()
    if not revision:
        raise c.Problem("RESOURCE_NOT_FOUND", "练习修订不存在", 404)
    return revision

def check_version(row, version):
    if version != row["content_version"]:
        raise c.Problem("VERSION_CONFLICT", "内容已被修改，请重新载入；草稿已保留", 409)

def lock_content(conn, owner, objects):
    """Use the same UUID lock order as graph commits and source promotion."""
    locked = {}
    unique = {(table.name, ident): table for table, ident in objects}
    for (name, ident), table in sorted(unique.items(), key=lambda item: str(item[0][1])):
        locked[(name, ident)] = c.require(db.one(conn, table, ident, owner, lock=True))
    return locked

def validate_sources(conn, owner, source_ids):
    from .api import allowed_source
    if len(set(source_ids)) != len(source_ids):
        raise c.Problem("VALIDATION_ERROR", "来源不能重复", 422)
    for ident in source_ids:
        allowed_source(conn, ident, owner)

class PlanUpdate(s.Input):
    expected_content_version: int = Field(gt=0, strict=True)
    content: s.PlanPayload

class Link(s.Input):
    plan_id: UUID
    stage_id: UUID | None = None
    task_id: UUID | None = None

class ExerciseEdit(s.Input):
    question_text: str = Field(min_length=1, max_length=100000)
    answer_text: str | None = Field(default=None, max_length=100000)
    explanation: str | None = Field(default=None, max_length=100000)
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    answer_source_ids: list[UUID] | None = Field(default=None, max_length=100)
    links: list[Link] = Field(default_factory=list, max_length=100)
    provenance: str = "generated"
    expected_version: int | None = Field(default=None, gt=0, strict=True)
    @model_validator(mode="after")
    def valid(self):
        if not self.question_text.strip() or self.provenance not in {"original", "adapted", "generated"}:
            raise ValueError("题干或来源类型不合法")
        if (len(set(self.source_ids)) != len(self.source_ids)
            or self.answer_source_ids is not None and len(set(self.answer_source_ids)) != len(self.answer_source_ids)
            or len({x.plan_id for x in self.links}) != len(self.links)):
            raise ValueError("来源或计划关联不能重复")
        return self

def save_links(conn, exercise, links):
    for item in sorted(links, key=lambda x: str(x.plan_id)):
        plan = c.require(db.one(conn, db.learning_plans, item.plan_id, exercise["owner_id"], lock=True))
        if plan["agent_id"] != exercise["agent_id"]:
            raise c.Problem("RESOURCE_NOT_FOUND", "关联计划不属于当前智能体", 404)
        stages = {x["id"]: x for x in plan["content"]["stages"]}
        stage = stages.get(str(item.stage_id)) if item.stage_id else None
        if item.stage_id and not stage or item.task_id and (not stage or str(item.task_id) not in {x["id"] for x in stage["tasks"]}):
            raise c.Problem("VALIDATION_ERROR", "计划阶段或任务不存在", 422)
    conn.execute(db.exercise_plan_links.delete().where(db.exercise_plan_links.c.exercise_id == exercise["id"]))
    for item in links:
        db.insert(conn, db.exercise_plan_links, owner_id=exercise["owner_id"], exercise_id=exercise["id"], **item.model_dump())

@router.put("/plans/{id}/content", operation_id="P-06")
def update_plan(id: UUID, body: PlanUpdate, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        c.require(db.one(conn, db.learning_plans, id, user["id"]))
        exercise_ids = list(conn.scalars(sa.select(db.exercise_plan_links.c.exercise_id).where(
            db.exercise_plan_links.c.plan_id == id, db.exercise_plan_links.c.owner_id == user["id"])))
        locked = lock_content(conn, user["id"], [(db.learning_plans, id), *[(db.exercises, x) for x in exercise_ids]])
        plan = locked[("learning_plans", id)]
        check_version(plan, body.expected_content_version)
        content = body.content.model_dump(mode="json", exclude={"title"})
        source_ids = {UUID(x) for stage in content["stages"] for task in stage["tasks"] for x in task["resource_source_ids"]}
        validate_sources(conn, user["id"], list(source_ids))
        stage_ids = {x["id"] for x in content["stages"]}
        task_ids = {x["id"] for st in content["stages"] for x in st["tasks"]}
        for other in conn.execute(sa.select(db.learning_plans.c.content).where(
            db.learning_plans.c.owner_id == user["id"], db.learning_plans.c.id != id)).scalars():
            foreign_ids = {st["id"] for st in other["stages"]} | {task["id"] for st in other["stages"] for task in st["tasks"]}
            if (stage_ids | task_ids) & foreign_ids:
                raise c.Problem("VALIDATION_ERROR", "不能使用其他计划的阶段或任务标识", 422)
        for link in conn.execute(sa.select(db.exercise_plan_links).where(db.exercise_plan_links.c.plan_id == id)).mappings().all():
            if link["stage_id"] and str(link["stage_id"]) not in stage_ids or link["task_id"] and str(link["task_id"]) not in task_ids:
                conn.execute(db.exercise_plan_links.delete().where(db.exercise_plan_links.c.id == link["id"]))
        plan = db.update(conn, db.learning_plans, id, content=content, content_version=plan["content_version"] + 1, user_edited=True)
        record_plan(conn, plan, "user_edited", user["id"])
        conn.execute(db.plan_sources.delete().where(db.plan_sources.c.plan_id == id))
        for i, source in enumerate(sorted(source_ids, key=str)):
            conn.execute(db.plan_sources.insert().values(plan_id=id, source_id=source, owner_id=user["id"], position=i, content_version=plan["content_version"]))
        return c.plan(conn, plan), 200, [["learning_plans", str(id)]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

@router.get("/agents/{id}/exercises", operation_id="Q-03")
def list_exercises(id: UUID, plan_id: UUID | None = None, stage_id: UUID | None = None, task_id: UUID | None = None,
    cursor: str | None = None, limit: int = Query(20, ge=1, le=100), ctx=D):
    conn, user, _ = ctx
    c.require(db.one(conn, db.agents, id, user["id"]))
    filters = [db.exercises.c.agent_id == id]
    if any((plan_id, stage_id, task_id)):
        q = sa.select(db.exercise_plan_links.c.exercise_id).where(db.exercise_plan_links.c.owner_id == user["id"])
        for key, value in (("plan_id", plan_id), ("stage_id", stage_id), ("task_id", task_id)):
            if value:
                q = q.where(db.exercise_plan_links.c[key] == value)
        filters.append(db.exercises.c.id.in_(q))
    rows, next_cursor = c.paginate(conn, db.exercises, user["id"], f"exercises:{id}:{plan_id}:{stage_id}:{task_id}", filters, cursor, limit)
    return c.envelope({"items": [exercise_dto(conn, x) for x in rows], "next_cursor": next_cursor})

@router.post("/agents/{id}/exercises", operation_id="Q-04")
def create_exercise(id: UUID, body: ExerciseEdit, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        c.require(db.one(conn, db.agents, id, user["id"], lock=True))
        validate_sources(conn, user["id"], list(set(body.source_ids) | set(body.answer_source_ids or [])))
        row = db.insert(conn, db.exercises, owner_id=user["id"], agent_id=id, position=0, question_text=body.question_text,
            provenance="generated", source_ids=jsonable_encoder(body.source_ids), origin_kind="user_edited", user_edited=True,
            known_answer_snapshot={"answer_text": body.answer_text, "explanation": body.explanation, "source_ids": jsonable_encoder(body.source_ids if body.answer_source_ids is None else body.answer_source_ids)} if body.answer_text or body.explanation else None)
        row = record_exercise(conn, row, "user_edited", user["id"])
        save_links(conn, row, body.links)
        return exercise_dto(conn, row), 201, [["exercises", str(row["id"])]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

@router.patch("/exercises/{id}", operation_id="Q-05")
def edit_exercise(id: UUID, body: ExerciseEdit, request: Request, ctx=D):
    conn, user, _ = ctx
    if body.expected_version is None:
        raise c.Problem("VALIDATION_ERROR", "修改必须提供expected_version", 422)
    def action():
        c.require(db.one(conn, db.exercises, id, user["id"]))
        old_plan_ids = list(conn.scalars(sa.select(db.exercise_plan_links.c.plan_id).where(
            db.exercise_plan_links.c.exercise_id == id, db.exercise_plan_links.c.owner_id == user["id"])))
        linked = set(old_plan_ids) | {link.plan_id for link in body.links}
        locked = lock_content(conn, user["id"], [(db.exercises, id), *[(db.learning_plans, x) for x in linked]])
        row = locked[("exercises", id)]
        check_version(row, body.expected_version)
        validate_sources(conn, user["id"], list(set(body.source_ids) | set(body.answer_source_ids or [])))
        row = db.update(conn, db.exercises, id, question_text=body.question_text, provenance="generated", source_ids=jsonable_encoder(body.source_ids),
            content_version=row["content_version"] + 1, user_edited=True,
            known_answer_snapshot={"answer_text": body.answer_text, "explanation": body.explanation, "source_ids": jsonable_encoder(body.source_ids if body.answer_source_ids is None else body.answer_source_ids)} if body.answer_text or body.explanation else None)
        row = record_exercise(conn, row, "user_edited", user["id"])
        save_links(conn, row, body.links)
        return exercise_dto(conn, row), 200, [["exercises", str(id)]]
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

@router.delete("/exercises/{id}", operation_id="Q-06")
def delete_exercise(id: UUID, request: Request, ctx=D):
    conn, user, _ = ctx
    def action():
        c.require(db.one(conn, db.exercises, id, user["id"], lock=True))
        conn.execute(db.exercises.delete().where(db.exercises.c.id == id))
        return {"deleted_id": id}, 200, []
    return c.idem(conn, request, user["id"], {}, action)

@router.get("/exercises/{id}/answer", operation_id="Q-07")
def existing_answer(id: UUID, version: int | None = Query(None, gt=0), ctx=D):
    row = c.require(db.one(ctx[0], db.exercises, id, ctx[1]["id"]))
    rev = revision_payload(ctx[0], row, version)
    payload = rev["payload"]
    source_ids = payload.get("answer_source_ids", payload.get("source_ids", []))
    sources = [c.source(x) for x in ctx[0].execute(sa.select(db.sources).where(
        db.sources.c.owner_id == ctx[1]["id"], db.sources.c.id.in_([UUID(str(x)) for x in source_ids]))).mappings()]
    return c.envelope({"exercise_id": id, "version": rev["version"], "available": bool(payload.get("answer_text") or payload.get("explanation")),
        "answer_text": payload.get("answer_text"), "explanation": payload.get("explanation"),
        "origin_kind": payload.get("answer_origin_kind", rev["origin_kind"]), "source_ids": source_ids, "sources": sources})

class ExerciseTask(s.Input):
    conversation_id: UUID
    expected_version: int = Field(gt=0, strict=True)
    answer_text: str = Field(default="", max_length=100000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=6)

def start_exercise_task(id, body, request, ctx, feedback=False):
    from .api import accept_chat
    conn, user, _ = ctx
    def action():
        row = c.require(db.one(conn, db.exercises, id, user["id"], lock=True))
        check_version(row, body.expected_version)
        conv = c.require(db.one(conn, db.conversations, body.conversation_id, user["id"]))
        if conv["agent_id"] != row["agent_id"]:
            raise c.Problem("RESOURCE_NOT_FOUND", "对话不属于该智能体", 404)
        if feedback and not body.answer_text.strip() and not body.attachment_ids:
            raise c.Problem("VALIDATION_ERROR", "请填写作答或附加图片", 422)
        send = s.Send(content_text=body.answer_text if feedback else f"请提供这道题的答案与解析：{row['question_text']}", attachment_ids=body.attachment_ids)
        result = accept_chat(conn, user["id"], body.conversation_id, send, **({"feedback": row} if feedback else {"answer": row}))
        data, status, refs = result
        return {**data, "conversation_id": body.conversation_id}, status, refs
    return c.idem(conn, request, user["id"], body.model_dump(mode="json"), action)

@router.post("/exercises/{id}/explanation-tasks", operation_id="Q-08")
def explanation(id: UUID, body: ExerciseTask, request: Request, ctx=D):
    return start_exercise_task(id, body, request, ctx)

@router.post("/exercises/{id}/feedback-tasks", operation_id="Q-09")
def feedback(id: UUID, body: ExerciseTask, request: Request, ctx=D):
    return start_exercise_task(id, body, request, ctx, True)

def register(app):
    app.include_router(router)
