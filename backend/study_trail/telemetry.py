"""Operational records deliberately contain no learning text or request payloads."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert

from . import db

RESOURCE_TABLES = {"users", "agents", "conversations", "knowledge_bases", "files", "learning_plans"}
verification_budget = ContextVar("verification_budget", default=None)


def inserted(conn, table, row):
    if table.name in RESOURCE_TABLES and (table is not db.users or row["role"] == "user"):
        conn.execute(
            insert(db.resource_events)
            .values(
                event_key=f"{table.name}:{row['id']}:created",
                owner_id=row.get("owner_id", row["id"]),
                resource_kind=table.name,
                resource_id=row["id"],
                occurred_at=row["created_at"],
                origin="live",
            )
            .on_conflict_do_nothing(index_elements=["event_key"])
        )
    if table is db.tasks and row["kind"] != "storage_cleanup":
        file = db.one(conn, db.files, row["file_id"]) if row["file_id"] else None
        conn.execute(
            db.task_run_facts.insert().values(
                task_id=row["id"],
                owner_id=row["owner_id"],
                source_agent_id=row["agent_id"],
                source_file_id=row["file_id"],
                file_extension=file["extension"] if file else None,
                kind=row["kind"],
                action=row["action"],
                accepted_at=row["created_at"],
                status=row["status"],
                model_config_id=(
                    row["config_snapshot"].get("embedding", {})
                    if row["kind"] == "file_process"
                    else row["config_snapshot"]
                ).get("config_id"),
            )
        )


def task_changed(conn, row):
    conn.execute(
        db.task_run_facts.update()
        .where(db.task_run_facts.c.task_id == row["id"])
        .values(
            status=row["status"],
            action=row["action"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            safe_error_code=row["error_code"],
        )
    )
    if row["status"] in {"failed", "stopped"}:
        conn.execute(
            db.invocation_attempts.update()
            .where(
                db.invocation_attempts.c.task_id == row["id"], db.invocation_attempts.c.status == "running"
            )
            .values(status="unknown", finished_at=db.now(), safe_error_code="WORKER_INTERRUPTED")
        )


@asynccontextmanager
async def attempt(task, category="model", model_kind="generative", config=None, tool_name=None):
    budget = verification_budget.get()
    if budget is not None:
        if budget["used"] >= budget["limit"]:
            from .core import Problem
            raise Problem("VERIFICATION_BUDGET_EXHAUSTED", "本轮真实调用额度已用尽", 409)
        budget["used"] += 1
    config = config or task.get("config_snapshot", {})
    ident = uuid4()
    purpose = task.get("purpose") or (
        "file_process"
        if task["kind"] == "file_process"
        else "prompt_assist"
        if task["kind"].startswith("prompt_")
        else "learning"
    )
    with db.engine.begin() as conn:
        conn.execute(
            db.invocation_attempts.insert().values(
                id=ident,
                operation_key=str(ident),
                attempt_no=1,
                task_id=task.get("id"),
                actor_id=task["owner_id"],
                purpose=purpose,
                category=category,
                model_kind=model_kind,
                config_id=config.get("config_id"),
                revision_id=config.get("revision_id"),
                tool_name=tool_name,
                status="running",
            )
        )
    status, code = "succeeded", None
    try:
        yield
    except asyncio.CancelledError:
        timed_out = task.get("deadline_at") and task["deadline_at"] <= db.now()
        status, code = ("failed", "TASK_TIMEOUT") if timed_out else ("stopped", "CANCELLED")
        raise
    except BaseException as exc:
        status, code = "failed", getattr(exc, "code", "PROVIDER_FAILED")
        raise
    finally:
        with db.engine.begin() as conn:
            conn.execute(
                db.invocation_attempts.update()
                .where(db.invocation_attempts.c.id == ident)
                .values(status=status, safe_error_code=code, finished_at=db.now())
            )
