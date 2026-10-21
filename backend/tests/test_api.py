import io
from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image

from study_trail import db
from study_trail.api import app
from study_trail.execution import assert_claim, claim, expire_leases, terminal

from .conftest import login


def post(client, path, body, key=None):
    return client.post("/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


def test_registration_validation_and_case_insensitive_login(client):
    login(client, "StudentABC")
    response = client.post(
        "/api/v1/auth/register",
        json={"login": "studentabc", "password": "password123", "confirm_password": "password123"},
    )
    assert response.status_code == 409
    response = client.post(
        "/api/v1/auth/login", json={"login": "  STUDENTabc  ", "password": "test password 123"}
    )
    assert response.status_code == 200
    response = client.post(
        "/api/v1/auth/register",
        json={"login": "other", "password": "secret", "confirm_password": "secret", "owner_id": "wrong"},
    )
    assert response.status_code == 422
    assert "secret" not in response.text


def test_csrf_and_cross_account_resources(client, resource):
    other = TestClient(app, raise_server_exceptions=False)
    other.headers["Origin"] = "http://127.0.0.1:5173"
    login(other)
    ident = resource["agent"]["id"]
    conv = resource["conversation"]["id"]
    assert other.get(f"/api/v1/agents/{ident}").status_code == 404
    assert other.get(f"/api/v1/conversations/{conv}/messages").status_code == 404
    assert other.post(f"/api/v1/agents/{ident}/conversations", json={}).status_code == 404
    assert (
        client.patch(
            "/api/v1/me", json={"display_name": "new"}, headers={"X-CSRF-Token": "invalid"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/me", json={"display_name": "new"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_password_revokes_every_session(client, student):
    other = TestClient(app, raise_server_exceptions=False)
    other.headers["Origin"] = "http://127.0.0.1:5173"
    response = other.post(
        "/api/v1/auth/login", json={"login": student["login"], "password": "test password 123"}
    )
    other.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
    response = client.post(
        "/api/v1/auth/password",
        json={
            "old_password": "test password 123",
            "new_password": "new password 456",
            "confirm_password": "new password 456",
        },
    )
    assert response.status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    assert other.get("/api/v1/me").status_code == 401


def test_idempotency_and_active_chat_limit(client, resource):
    path = f"/conversations/{resource['conversation']['id']}/messages"
    key = str(uuid4())
    first = post(client, path, {"content_text": "解释矩阵乘法"}, key)
    second = post(client, path, {"content_text": "解释矩阵乘法"}, key)
    assert first.status_code == second.status_code == 202
    assert first.json()["data"] == second.json()["data"]
    assert post(client, path, {"content_text": "different"}, key).status_code == 409
    assert post(client, path, {"content_text": "另一个请求"}).json()["error"]["code"] == "CONVERSATION_BUSY"
    with db.engine.begin() as conn:
        assert (
            conn.execute(
                sa.select(sa.func.count()).select_from(db.messages).where(db.messages.c.role == "user")
            ).scalar()
            == 1
        )


def test_queued_stop_and_retry_same_user_message(client, resource):
    path = f"/conversations/{resource['conversation']['id']}/messages"
    data = post(client, path, {"content_text": "请解释矩阵"}).json()["data"]
    t = data["task"]["id"]
    m = data["user_message"]["id"]
    stopped = post(client, f"/tasks/{t}/stop", {}).json()["data"]
    assert stopped["status"] == "stopped"
    assert claim("test-worker") is None
    retried = post(client, f"/messages/{m}/retry", {"previous_task_id": t}).json()["data"]
    assert retried["user_message"]["id"] == m
    assert retried["task"]["attempt_no"] == 2
    assert retried["task"]["id"] != t
    assert post(client, f"/messages/{m}/retry", {"previous_task_id": t}).status_code == 409


def test_sse_snapshot_terminal_and_revision_validation(client, resource):
    data = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "问题"}
    ).json()["data"]
    t = data["task"]["id"]
    post(client, f"/tasks/{t}/stop", {})
    response = client.get(f"/api/v1/tasks/{t}/events?after_revision=0")
    assert response.status_code == 200
    assert "event: snapshot" in response.text and "event: stopped" in response.text
    assert client.get(f"/api/v1/tasks/{t}/events?after_revision=99999").status_code == 422


def test_image_binding_isolation_and_six_limit(client, resource):
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    conv = resource["conversation"]["id"]
    response = client.post(
        f"/api/v1/conversations/{conv}/images",
        files={"file": ("test.png", buf.getvalue(), "image/png")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    image = response.json()["data"]
    assert "storage_key" not in image
    response = post(client, f"/conversations/{conv}/messages", {"attachment_ids": [image["id"]]})
    assert response.status_code == 202, response.text
    assert client.delete(f"/api/v1/attachments/{image['id']}").status_code == 409
    assert client.get(image["content_url"]).headers["cache-control"] == "private, no-store"
    other = TestClient(app, raise_server_exceptions=False)
    other.headers["Origin"] = "http://127.0.0.1:5173"
    login(other)
    assert other.get(image["content_url"]).status_code == 404
    assert (
        post(
            client, f"/conversations/{conv}/messages", {"attachment_ids": [str(uuid4()) for _ in range(7)]}
        ).status_code
        == 422
    )


def test_file_batch_partial_acceptance_and_preview_gate(client, student):
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    response = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/files",
        files=[
            ("files", ("valid.txt", "可读资料".encode(), "text/plain")),
            ("files", ("bad.exe", b"x", "application/octet-stream")),
        ],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    result = response.json()["data"]["items"]
    assert result[0]["file"]["status"] == "processing"
    assert result[1]["error"]
    assert client.get(f"/api/v1/files/{result[0]['file']['id']}/preview").status_code == 409
    assert post(client, f"/tasks/{result[0]['task']['id']}/stop", {}).status_code == 409


def test_expired_lease_rejects_late_commit(client, resource):
    data = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "问题"}
    ).json()["data"]
    claimed = claim("test-worker")
    with db.engine.begin() as conn:
        db.update(conn, db.tasks, claimed["id"], lease_expires_at=db.now() - timedelta(seconds=1))
    expire_leases()
    assert client.get(f"/api/v1/tasks/{data['task']['id']}").json()["data"]["error"]["code"] == "WORKER_LOST"
    import asyncio

    import pytest

    with db.engine.begin() as conn:
        with pytest.raises(asyncio.CancelledError):
            assert_claim(conn, claimed)


def test_stop_commit_lock_order_both_outcomes(client, resource):
    path = f"/conversations/{resource['conversation']['id']}/messages"
    data = post(client, path, {"content_text": "问题"}).json()["data"]
    claimed = claim("test-worker")
    post(client, f"/tasks/{claimed['id']}/stop", {})
    import asyncio

    import pytest

    with db.engine.begin() as conn:
        with pytest.raises(asyncio.CancelledError):
            assert_claim(conn, claimed)
        row = db.one(conn, db.tasks, claimed["id"], lock=True)
        terminal(conn, row, "stopped")
    next_data = post(client, path, {"content_text": "下一题"}).json()["data"]
    claimed = claim("test-worker")
    with db.engine.begin() as conn:
        terminal(conn, assert_claim(conn, claimed), "succeeded")
    assert post(client, f"/tasks/{claimed['id']}/stop", {}).json()["data"]["status"] == "succeeded"


def test_cursor_scope_tampering_and_deleted_idempotency(client, resource):
    client.post("/api/v1/agents", json={"name": "第二个", "system_prompt": "解释。"})
    response = client.get("/api/v1/agents?limit=1").json()["data"]
    cursor = response["next_cursor"]
    assert cursor
    assert client.get("/api/v1/agents?limit=1&cursor=" + cursor).status_code == 200
    assert client.get("/api/v1/knowledge-bases?cursor=" + cursor).status_code == 422
    assert client.get("/api/v1/agents?cursor=" + cursor + "x").status_code == 422
    key = str(uuid4())
    path = f"/conversations/{resource['conversation']['id']}/messages"
    post(client, path, {"content_text": "问题"}, key)
    client.delete("/api/v1/conversations/" + resource["conversation"]["id"])
    # Original parent is gone, so access remains 404 rather than resurrecting it.
    assert post(client, path, {"content_text": "问题"}, key).status_code == 410


def test_database_guards_cross_owner_private_references(client, resource):
    other = TestClient(app, raise_server_exceptions=False)
    other.headers["Origin"] = "http://127.0.0.1:5173"
    foreign = login(other)
    import pytest

    with pytest.raises(sa.exc.DBAPIError):
        with db.engine.begin() as conn:
            db.insert(
                conn,
                db.tasks,
                owner_id=UUID(foreign["id"]),
                kind="chat",
                agent_id=UUID(resource["agent"]["id"]),
                conversation_id=UUID(resource["conversation"]["id"]),
            )


def test_openapi_contract_has_85_operations_and_public_models(client):
    spec = client.get("/openapi.json").json()
    operations = [
        value["operationId"]
        for path in spec["paths"].values()
        for method, value in path.items()
        if method in {"get", "post", "patch", "delete"}
    ]
    assert len(operations) == len(set(operations)) == 85
    assert "StudentDTO" in spec["components"]["schemas"]
    assert "password_hash" not in json_schema(spec["components"]["schemas"]["StudentDTO"])


def json_schema(value):
    import json

    return json.dumps(value)
