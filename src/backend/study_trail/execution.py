import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder

from . import core as c
from . import db
from .config import settings

log = logging.getLogger("study_trail.worker")


def terminal(conn, row, status, code=None, message=None, result=None):
    row = db.update(
        conn,
        db.tasks,
        row["id"],
        status=status,
        finished_at=db.now(),
        revision=row["revision"] + 1,
        error_code=code,
        error_message=message,
        **({"result_snapshot": jsonable_encoder(result)} if result is not None else {}),
    )
    if row["assistant_message_id"]:
        db.update(conn, db.messages, row["assistant_message_id"], response_status=status)
    if row["file_id"] and status == "failed":
        db.update(conn, db.files, row["file_id"], status="failed", error_code=code, error_message=message)
    return row


def cleanup_retry(conn, row):
    c.cleanup(conn, row["owner_id"], row["input_snapshot"]["storage_key"], row["attempt_no"] + 1, row["id"])


def expire_leases():
    with db.engine.begin() as conn:
        rows = (
            conn.execute(
                sa.select(db.tasks)
                .where(
                    db.tasks.c.status == "running",
                    sa.or_(db.tasks.c.lease_expires_at < db.now(), db.tasks.c.deadline_at < db.now()),
                )
                .order_by(db.tasks.c.id)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .all()
        )
        for row in rows:
            stopped = row["cancel_requested"]
            timed = row["deadline_at"] < db.now()
            terminal(
                conn,
                row,
                "stopped" if stopped else "failed",
                None if stopped else "TASK_TIMEOUT" if timed else "WORKER_LOST",
                None if stopped else "任务处理超时" if timed else "后台执行中断，请手动重试",
            )
            if row["kind"] == "storage_cleanup":
                cleanup_retry(conn, row)


def claim(worker_id):
    with db.engine.begin() as conn:
        # Global capacity, also when another independent worker is launched.
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(734811)"))
        ai = ["chat", "prompt_generate", "prompt_polish"]
        files = ["file_process", "storage_cleanup"]

        def count(kinds):
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(db.tasks)
                .where(db.tasks.c.kind.in_(kinds), db.tasks.c.status == "running")
            ).scalar()

        kinds = (ai if count(ai) < settings().ai_concurrency else []) + (
            files if count(files) < settings().file_concurrency else []
        )
        if not kinds:
            return None
        row = (
            conn.execute(
                sa.select(db.tasks)
                .where(
                    db.tasks.c.status == "queued",
                    db.tasks.c.kind.in_(kinds),
                    sa.or_(db.tasks.c.next_attempt_at.is_(None), db.tasks.c.next_attempt_at <= db.now()),
                )
                .order_by(db.tasks.c.queued_at, db.tasks.c.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        now = db.now()
        row = db.update(
            conn,
            db.tasks,
            row["id"],
            status="running",
            started_at=now,
            deadline_at=now + timedelta(seconds=row["timeout_seconds"]),
            claim_token=uuid4(),
            lease_owner=worker_id,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=30),
            revision=row["revision"] + 1,
        )
        if row["assistant_message_id"]:
            db.update(conn, db.messages, row["assistant_message_id"], response_status="running")
        return dict(row)


def assert_claim(conn, task):
    row = db.one(conn, db.tasks, task["id"], task["owner_id"], lock=True)
    if (
        not row
        or row["status"] != "running"
        or row["claim_token"] != task["claim_token"]
        or row["lease_expires_at"] <= db.now()
    ):
        raise asyncio.CancelledError()
    if row["cancel_requested"]:
        raise asyncio.CancelledError()
    if row["deadline_at"] <= db.now():
        raise c.Problem("TASK_TIMEOUT", "任务处理超时，请重试", 409)
    return row


def publish(task, phase=None, text=None, sources=None):
    with db.engine.begin() as conn:
        row = assert_claim(conn, task)
        data = {"revision": row["revision"] + 1}
        if phase:
            data["phase"] = phase
        if text is not None:
            data["preview_text"] = text
            if row["assistant_message_id"]:
                db.update(conn, db.messages, row["assistant_message_id"], content_text=text)
        if sources is not None:
            data["result_snapshot"] = {**row["result_snapshot"], "sources": jsonable_encoder(sources)}
        db.update(conn, db.tasks, row["id"], **data)


async def run_task(task):
    from .files import chunks, extract
    from .providers import Gateway
    from .workflow import run_chat, run_prompt

    async def work():
        if task["kind"] == "chat":
            return await run_chat(task)
        if task["kind"].startswith("prompt_"):
            return await run_prompt(task)
        if task["kind"] == "storage_cleanup":
            await asyncio.to_thread(
                c.storage_path(task["input_snapshot"]["storage_key"]).unlink, missing_ok=True
            )
            with db.engine.begin() as conn:
                terminal(conn, assert_claim(conn, task), "succeeded")
            return
        with db.engine.begin() as conn:
            row = c.require(db.one(conn, db.files, task["file_id"], task["owner_id"]))
            path = c.storage_path(row["storage_key"])
            ext = row["extension"]
        publish(task, "parse")
        text, metadata = await asyncio.to_thread(extract, path, ext)
        candidate = chunks(text, metadata)
        with db.engine.begin() as conn:
            assert_claim(conn, task)
            db.update(conn, db.files, row["id"], text_snapshot=text, text_metadata=metadata)
        publish(task, "embed")
        gateway = Gateway(task)
        vectors = []
        for start in range(0, len(candidate), 32):
            vectors.extend(await gateway.embed([r["content_text"] for r in candidate[start : start + 32]]))
        with db.engine.begin() as conn:
            current = assert_claim(conn, task)
            live = c.require(db.one(conn, db.files, row["id"], task["owner_id"], lock=True))
            version = live["parse_version"] if not live["indexed_at"] else live["parse_version"] + 1
            conn.execute(db.document_chunks.delete().where(db.document_chunks.c.file_id == row["id"]))
            for chunk, vector in zip(candidate, vectors, strict=True):
                db.insert(
                    conn,
                    db.document_chunks,
                    owner_id=task["owner_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    file_id=row["id"],
                    parse_version=version,
                    embedding=vector,
                    **chunk,
                )
            db.update(
                conn,
                db.files,
                row["id"],
                status="ready",
                parse_version=version,
                text_snapshot=text,
                text_metadata=metadata,
                embedding_model_id="BAAI/bge-m3",
                indexed_at=db.now(),
                error_code=None,
                error_message=None,
            )
            terminal(conn, current, "succeeded", result={"file_id": str(row["id"])})

    execution = asyncio.create_task(work())

    async def monitor():
        last_heartbeat = 0
        while not execution.done():
            with db.engine.begin() as conn:
                row = assert_claim(conn, task)
                clock = asyncio.get_running_loop().time()
                if clock - last_heartbeat >= 5:
                    db.update(
                        conn,
                        db.tasks,
                        row["id"],
                        heartbeat_at=db.now(),
                        lease_expires_at=db.now() + timedelta(seconds=30),
                    )
                    last_heartbeat = clock
            await asyncio.sleep(0.25)

    monitoring = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait([execution, monitoring], return_when=asyncio.FIRST_COMPLETED)
        for result in done:
            await result
        if not execution.done():
            execution.cancel()
            await execution
    except asyncio.CancelledError:
        execution.cancel()
        with db.engine.begin() as conn:
            row = db.one(conn, db.tasks, task["id"], lock=True)
            if row and row["status"] == "running" and row["claim_token"] == task["claim_token"]:
                terminal(
                    conn,
                    row,
                    "stopped" if row["cancel_requested"] else "failed",
                    None if row["cancel_requested"] else "WORKER_LOST",
                    None if row["cancel_requested"] else "后台执行中断，请重试",
                )
    except Exception as exc:
        execution.cancel()
        code = exc.code if isinstance(exc, c.Problem) else "TASK_FAILED"
        message = exc.message if isinstance(exc, c.Problem) else "处理未完成，请重试"
        log.warning("task=%s code=%s exception=%s", task["id"], code, type(exc).__name__)
        with db.engine.begin() as conn:
            row = db.one(conn, db.tasks, task["id"], lock=True)
            if row and row["status"] == "running" and row["claim_token"] == task["claim_token"]:
                terminal(
                    conn,
                    row,
                    "stopped" if row["cancel_requested"] else "failed",
                    None if row["cancel_requested"] else code,
                    None if row["cancel_requested"] else message,
                )
                if row["kind"] == "storage_cleanup":
                    cleanup_retry(conn, row)
    finally:
        monitoring.cancel()
        await asyncio.gather(execution, monitoring, return_exceptions=True)


async def worker():
    worker_id = str(uuid4())
    active = set()
    expire_leases()
    log.info("worker=%s ready", worker_id)
    try:
        while True:
            active = {t for t in active if not t.done()}
            expire_leases()
            # Expired staged attachments are rechecked under a lock before logical removal.
            with db.engine.begin() as conn:
                stale = (
                    conn.execute(
                        sa.select(db.attachments)
                        .where(db.attachments.c.state == "staged", db.attachments.c.expires_at < db.now())
                        .with_for_update(skip_locked=True)
                    )
                    .mappings()
                    .all()
                )
                for a in stale:
                    c.cleanup(conn, a["owner_id"], a["storage_key"])
                    conn.execute(db.attachments.delete().where(db.attachments.c.id == a["id"]))
            task = claim(worker_id)
            if task:
                active.add(asyncio.create_task(run_task(task)))
            else:
                await asyncio.sleep(0.5)
    finally:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(worker())
    except KeyboardInterrupt:
        pass
