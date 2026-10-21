import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4
import pytest
from PIL import Image
from study_trail import db, core as c
from study_trail import workflow, api
from study_trail.execution import claim, run_task
from study_trail.student_constraints import constraints
from .test_api import post
from .test_workflow import payload


@pytest.mark.parametrize("winner", ["stop", "commit"])
def test_stop_and_commit_compete_with_barriers(client, resource, monkeypatch, winner):
    post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "制定学习计划"}
    )
    claimed = claim("race-worker")
    acquired = threading.Event()
    release = threading.Event()
    state = {
        "task": claimed,
        "action": "plan_create",
        "sources": [],
        "answer": "候选",
        "plan": payload(),
        "exercises": [],
    }
    if winner == "commit":
        original = workflow.assert_claim

        def held(conn, task):
            row = original(conn, task)
            acquired.set()
            assert release.wait(8)
            return row

        monkeypatch.setattr(workflow, "assert_claim", held)
    else:
        original = api.stop_row

        def held(conn, row):
            result = original(conn, row)
            acquired.set()
            assert release.wait(8)
            return result

        monkeypatch.setattr(api, "stop_row", held)

    def commit():
        try:
            asyncio.run(workflow.atomic_commit(state))
            return "committed"
        except asyncio.CancelledError:
            return "cancelled"

    def stop():
        return post(client, f"/tasks/{claimed['id']}/stop", {}).json()["data"]["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(commit if winner == "commit" else stop)
        assert acquired.wait(8)
        second = pool.submit(stop if winner == "commit" else commit)
        release.set()
        a, b = first.result(timeout=8), second.result(timeout=8)
    plans = client.get(f"/api/v1/agents/{resource['agent']['id']}/plans").json()["data"]["items"]
    if winner == "commit":
        assert a == "committed" and b == "succeeded" and len(plans) == 1
    else:
        assert b == "cancelled" and len(plans) == 0


async def test_deleted_source_cannot_publish_plan(client, resource, monkeypatch):
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    owner = UUID(resource["student"]["id"])
    with db.engine.begin() as conn:
        file = db.insert(
            conn,
            db.files,
            owner_id=owner,
            knowledge_base_id=UUID(kb["id"]),
            original_name="资料.txt",
            extension=".txt",
            media_type="text/plain",
            storage_key=c.store_bytes(b"file text"),
            sha256=b"a" * 32,
            byte_size=9,
            status="ready",
            text_snapshot="file text",
        )
    post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "制定学习计划"}
    )
    claimed = claim("test")
    source = {
        "id": str(uuid4()),
        "kind": "file",
        "file_id": str(file["id"]),
        "chunk_id": None,
        "title": "资料.txt",
        "excerpt": "file text",
        "url": None,
        "locator": {},
        "status": "available",
        "captured_at": db.now().isoformat(),
    }
    state = {
        "task": claimed,
        "action": "plan_create",
        "sources": [source],
        "answer": "候选",
        "plan": payload(),
        "exercises": [],
    }
    assert client.delete("/api/v1/files/" + str(file["id"])).status_code == 200
    monkeypatch.setattr(workflow, "run_chat", lambda task: workflow.atomic_commit(state))
    await run_task(claimed)
    snapshot = client.get("/api/v1/tasks/" + str(claimed["id"])).json()["data"]
    assert snapshot["status"] == "failed" and snapshot["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert client.get(f"/api/v1/agents/{resource['agent']['id']}/plans").json()["data"]["items"] == []


def test_deadline_and_time_need_student_evidence():
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert constraints("制定计划，没有期限", now)["deadline"] is None
    assert constraints("两周内完成，总共只有2小时", now) == {
        "deadline": "2026-10-19",
        "time_limit_minutes": 120,
    }
    assert constraints("截止2026年10月15日，每天30分钟", now) == {
        "deadline": "2026-10-15",
        "time_limit_minutes": None,
    }


def test_invalid_path_and_request_size(client, resource):
    assert client.get("/api/v1/agents/not-a-uuid").status_code == 422
    response = client.post(
        "/api/v1/agents", content=b"x" * (2 * 1024**2 + 1), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413 and response.json()["error"]["code"] == "REQUEST_TOO_LARGE"

    def large_chunks():
        for _ in range(3):
            yield b"x" * 1024**2

    chunked = client.post(
        "/api/v1/agents", content=large_chunks(), headers={"Content-Type": "application/json"}
    )
    assert chunked.status_code == 413 and chunked.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_deleted_image_idempotency(client, resource):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    key = str(uuid4())
    path = f"/api/v1/conversations/{resource['conversation']['id']}/images"

    def upload():
        return client.post(
            path, files={"file": ("test.png", buf.getvalue(), "image/png")}, headers={"Idempotency-Key": key}
        )

    image = upload().json()["data"]
    assert client.delete("/api/v1/attachments/" + image["id"]).status_code == 200
    assert upload().status_code == 410
