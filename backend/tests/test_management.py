import json
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from study_trail import core, db, platform
from study_trail.api import app
from study_trail.execution import claim, run_task
from study_trail.providers import Gateway
from study_trail.telemetry import attempt

from .test_api import post
from .test_workflow import FakeGateway


@pytest.fixture
def admin():
    with db.engine.begin() as conn:
        user = db.insert(
            conn,
            db.users,
            login="admin_test",
            login_normalized="admin_test",
            display_name="管理测试",
            password_hash=core.password_hash.hash("admin-test-password"),
            role="admin",
        )
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers["Origin"] = "http://127.0.0.1:5173"
        response = client.post(
            "/api/admin/auth/login", json={"login": "admin_test", "password": "admin-test-password"}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
        yield client


def change(admin, path, body, key=None, method="PATCH"):
    return admin.request(
        method, "/api/admin" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def configs(admin, kind):
    response = admin.get("/api/admin/configs/" + kind)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_realms_csrf_registration_and_private_resources(client, student, admin, resource):
    assert client.get("/api/admin/users").status_code == 401
    assert admin.get("/api/v1/agents").status_code == 401
    assert admin.get("/api/v1/agents/" + resource["agent"]["id"]).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/register",
            json={
                "login": "other_admin",
                "password": "password123",
                "confirm_password": "password123",
                "role": "admin",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/auth/login", json={"login": student["login"], "password": "test password 123"}
        ).status_code
        == 401
    )
    admin.headers["X-CSRF-Token"] = "wrong"
    assert change(admin, "/settings", {"expected_version": 1, "batch_max_files": 2}).status_code == 403


async def test_disable_revokes_sessions_but_preserves_accepted_work(client, resource, admin, monkeypatch):
    accepted = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "解释矩阵"}
    ).json()["data"]
    user = admin.get("/api/admin/users").json()["data"]["items"][0]
    disabled = change(
        admin, f"/users/{user['id']}/status", {"enabled": False, "expected_version": user["lock_version"]}
    )
    assert disabled.status_code == 200, disabled.text
    assert client.get("/api/v1/me").status_code == 401
    with db.engine.begin() as conn:
        assert db.one(conn, db.tasks, UUID(accepted["task"]["id"]))["status"] == "queued"
    monkeypatch.setattr("study_trail.providers.Gateway", FakeGateway)
    monkeypatch.setattr("study_trail.workflow.Gateway", FakeGateway)
    await run_task(claim("management-test"))
    with db.engine.begin() as conn:
        assert db.one(conn, db.tasks, UUID(accepted["task"]["id"]))["status"] == "succeeded"
    enabled = change(
        admin,
        f"/users/{user['id']}/status",
        {"enabled": True, "expected_version": disabled.json()["data"]["lock_version"]},
    )
    assert enabled.status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"login": user["login"], "password": "test password 123"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/tasks/" + accepted["task"]["id"]).json()["data"]["status"] == "succeeded"


def test_config_cas_idempotency_secrets_and_frozen_credentials(admin, client, resource):
    accepted = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "矩阵"}
    ).json()["data"]
    with db.engine.begin() as conn:
        before = dict(db.one(conn, db.tasks, UUID(accepted["task"]["id"]))["config_snapshot"])
    row = next(r for r in configs(admin, "model") if r["stable_id"] == before["model_id"])
    key = str(uuid4())
    body = {
        "name": "新版模型名称",
        "expected_version": row["lock_version"],
        "secret_updates": {"api_key": "NEW-SECRET-NEVER-ECHO"},
    }
    response = change(admin, "/configs/model/" + row["id"], body, key)
    assert response.status_code == 200, response.text
    assert "NEW-SECRET" not in response.text
    assert change(admin, "/configs/model/" + row["id"], body, key).json()["data"] == response.json()["data"]
    assert change(admin, "/configs/model/" + row["id"], {**body, "name": "other"}, key).status_code == 409
    assert change(admin, "/configs/model/" + row["id"], body).status_code == 409
    assert Gateway({"config_snapshot": before}).connection()[1]["Authorization"] == "Bearer test-secret"
    with db.engine.begin() as conn:
        after = platform.snapshot(conn, before["model_id"])
    assert (
        Gateway({"config_snapshot": after}).connection()[1]["Authorization"] == "Bearer NEW-SECRET-NEVER-ECHO"
    )
    cleared = change(
        admin,
        "/configs/model/" + row["id"],
        {"expected_version": response.json()["data"]["lock_version"], "secret_updates": {"api_key": " "}},
    )
    assert cleared.status_code == 200
    with db.engine.begin() as conn:
        kept = platform.snapshot(conn, before["model_id"])
        records = conn.execute(sa.select(db.idempotency_records.c.response_snapshot)).scalars().all()
        logs = conn.execute(sa.select(db.admin_operation_logs)).mappings().all()
    assert (
        Gateway({"config_snapshot": kept}).connection()[1]["Authorization"] == "Bearer NEW-SECRET-NEVER-ECHO"
    )
    assert "NEW-SECRET" not in json.dumps([records, [dict(r) for r in logs]], default=str)


def test_prompt_replacement_required_configuration_and_atomic_failure(admin):
    old = next(r for r in configs(admin, "prompt") if r["purpose"] == "intent")
    assert (
        change(
            admin,
            "/configs/prompt/" + old["id"] + "/status",
            {"enabled": False, "expected_version": old["lock_version"]},
        ).status_code
        == 409
    )
    created = change(
        admin,
        "/configs/prompt",
        {
            "stable_id": "alternate-intent",
            "name": "新的意图指令",
            "purpose": "intent",
            "payload": {"purpose": "intent", "body": "新的平台指令"},
        },
        method="POST",
    )
    assert created.status_code == 201, created.text
    row = created.json()["data"]
    status = {"enabled": True, "expected_version": row["lock_version"]}
    assert change(admin, "/configs/prompt/" + row["id"] + "/status", status).status_code == 409
    switched = change(
        admin,
        "/configs/prompt/" + row["id"] + "/status",
        {**status, "replace_active_id": old["id"], "confirm_replace": True},
    )
    assert switched.status_code == 200, switched.text
    assert len([r for r in configs(admin, "prompt") if r["purpose"] == "intent" and r["enabled"]]) == 1
    broken = change(
        admin,
        "/configs/prompt/" + row["id"],
        {
            "expected_version": switched.json()["data"]["lock_version"],
            "payload": {"purpose": "intent", "body": ""},
        },
    )
    assert broken.status_code == 422
    fresh = admin.get("/api/admin/configs/prompt/" + row["id"]).json()["data"]
    assert fresh["payload"]["body"] == "新的平台指令"
    assert fresh["revision_no"] == 1


def test_limits_propagation_and_hard_bounds(admin, client, resource):
    current = admin.get("/api/admin/settings").json()["data"]
    assert (
        change(admin, "/settings", {"expected_version": current["lock_version"], "tool_slots": 7}).status_code
        == 422
    )
    saved = change(
        admin,
        "/settings",
        {
            "expected_version": current["lock_version"],
            "file_max_bytes": 20,
            "batch_max_files": 1,
            "ai_timeout_seconds": 9,
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/v1/runtime-limits").json()["data"] == {
        "file_max_bytes": 20,
        "batch_max_files": 1,
        "image_max_bytes": 10485760,
        "message_max_images": 6,
        "model_readiness": {"default_generation": True, "embedding": True, "rerank": True},
    }
    accepted = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "hello"}
    ).json()["data"]
    with db.engine.begin() as conn:
        assert db.one(conn, db.tasks, UUID(accepted["task"]["id"]))["timeout_seconds"] == 9
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    upload = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/files",
        files=[("files", ("x.txt", b"x" * 30, "text/plain"))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert upload.status_code == 200
    assert upload.json()["data"]["items"][0]["error"]["code"] == "VALIDATION_ERROR"


def test_embedding_change_rejected_during_file_processing(admin, client, resource):
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    uploaded = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/files",
        files=[("files", ("x.txt", b"matrix", "text/plain"))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert uploaded.status_code == 202
    row = next(r for r in configs(admin, "model") if r["payload"]["model_kind"] == "embedding")
    before = row["revision_no"]
    edited = change(
        admin,
        "/configs/model/" + row["id"],
        {
            "expected_version": row["lock_version"],
            "payload": {**row["payload"], "model_revision": "different-model"},
        },
    )
    assert edited.status_code == 409, edited.text
    assert edited.json()["error"]["code"] == "EMBEDDING_INDEX_INCOMPATIBLE"
    assert admin.get("/api/admin/configs/model/" + row["id"]).json()["data"]["revision_no"] == before
    rotated = change(
        admin,
        "/configs/model/" + row["id"],
        {"expected_version": row["lock_version"], "secret_updates": {"api_key": "rotated"}},
    )
    assert rotated.status_code == 200


async def test_statistics_attempts_deletion_and_private_projection(admin, client, resource):
    accepted = post(
        client,
        f"/conversations/{resource['conversation']['id']}/messages",
        {"content_text": "PRIVATE-LEARNING-BODY"},
    ).json()["data"]
    with db.engine.begin() as conn:
        task = dict(db.one(conn, db.tasks, UUID(accepted["task"]["id"])))
    async with attempt(task):
        pass
    with pytest.raises(core.Problem):
        async with attempt(task):
            raise core.Problem("MODEL_UNAVAILABLE", "PRIVATE-UPSTREAM-ERROR", 503)
    async with attempt({**task, "id": None, "purpose": "connection_test"}):
        pass
    report = admin.get("/api/admin/reports/calls").json()["data"]
    assert report["total_attempts"] == 2
    assert report["counts"]["succeeded"] == report["counts"]["failed"] == 1
    assert sum(report["counts"].values()) == report["total_attempts"]
    assert client.delete("/api/v1/agents/" + resource["agent"]["id"]).status_code == 200
    detail = admin.get("/api/admin/task-runs/" + accepted["task"]["id"])
    assert detail.json()["data"]["resource_deleted"]
    for path in [
        "/task-runs",
        "/file-runs",
        "/agents",
        "/operation-logs",
        "/task-runs/" + accepted["task"]["id"],
        "/task-runs/" + accepted["task"]["id"] + "/calls",
    ]:
        response = admin.get("/api/admin" + path)
        assert response.status_code == 200, response.text
        assert "PRIVATE" not in response.text
        assert "input_snapshot" not in response.text and "password_hash" not in response.text
    usage = admin.get("/api/admin/reports/usage").json()["data"]
    assert usage["current_totals"]["agents"] == 0
    assert usage["new_counts"]["agents"]["value"] == 1
    assert usage["new_counts"]["agents"]["state"] == "partial"


def test_cursor_binding_unknown_filters_and_deletion_requires_version(admin):
    page = admin.get("/api/admin/configs/prompt?limit=1").json()["data"]
    cursor = page["next_cursor"]
    assert cursor
    assert admin.get("/api/admin/configs/prompt", params={"limit": 1, "cursor": cursor}).status_code == 200
    assert (
        admin.get("/api/admin/configs/prompt", params={"purpose": "intent", "cursor": cursor}).status_code
        == 422
    )
    assert admin.get("/api/admin/task-runs?status=invalid").status_code == 422
    assert admin.get("/api/admin/reports/usage?arbitrary=x").status_code == 422
    row = configs(admin, "skill")[0]
    assert admin.delete("/api/admin/configs/skill/" + row["id"]).status_code == 422
    assert admin.get("/api/admin/configs/skill/" + row["id"]).status_code == 200
    historical = admin.get("/api/admin/reports/calls?from=2020-01-01T00:00:00Z&to=2020-01-02T00:00:00Z").json()["data"]
    assert historical["coverage"]["state"] == "not_collected"
    assert historical["coverage"]["value"] is None


async def test_draft_connection_probe_does_not_save_and_reuses_idempotency(admin, monkeypatch):
    seen = []

    async def complete(self, *args, **kwargs):
        seen.append(self.task)
        return "OK"

    monkeypatch.setattr(Gateway, "complete", complete)
    row = next(r for r in configs(admin, "model") if r["payload"]["model_kind"] == "generative")
    body = {
        "kind": "model",
        "config_id": row["id"],
        "expected_version": row["lock_version"],
        "draft": {
            "payload": {**row["payload"], "endpoint": "https://draft.invalid/v1"},
            "secret_updates": {"api_key": "DRAFT-SECRET"},
        },
    }
    key = str(uuid4())
    response = change(admin, "/config-tests", body, key, "POST")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["outcome"] == "success"
    assert change(admin, "/config-tests", body, key, "POST").json()["data"] == response.json()["data"]
    assert len(seen) == 1
    fresh = admin.get("/api/admin/configs/model/" + row["id"]).json()["data"]
    assert fresh["lock_version"] == row["lock_version"]
    assert fresh["payload"]["endpoint"] != "https://draft.invalid/v1"
    assert "DRAFT-SECRET" not in response.text


@pytest.mark.parametrize("code,outcome", [("MODEL_TIMEOUT", "timeout"), ("CREDENTIALS_INVALID", "failure"), ("MODEL_UNAVAILABLE", "failure")])
async def test_connection_failures_are_safe_and_categorized(admin, monkeypatch, code, outcome):
    async def broken(*args, **kwargs):
        raise core.Problem(code, "upstream must not be exposed", 503)
    monkeypatch.setattr(Gateway, "complete", broken)
    row = next(r for r in configs(admin, "model") if r["payload"]["model_kind"] == "generative")
    response = change(admin, "/config-tests", {"kind": "model", "config_id": row["id"]}, method="POST")
    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == outcome
    assert response.json()["data"]["error_code"] == code
    assert "upstream" not in response.text


def test_concurrent_config_edits_have_one_winner(admin):
    from concurrent.futures import ThreadPoolExecutor

    row = configs(admin, "skill")[0]

    def edit(name):
        return change(
            admin, "/configs/skill/" + row["id"], {"name": name, "expected_version": row["lock_version"]}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(edit, ["first-editor", "second-editor"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = admin.get("/api/admin/configs/skill/" + row["id"]).json()["data"]
    assert current["revision_no"] == row["revision_no"] + 1


def test_audit_failure_rolls_back_configuration(admin, monkeypatch):
    from study_trail import admin as admin_module

    row = configs(admin, "skill")[0]

    def failed_audit(*args, **kwargs):
        raise core.Problem("AUDIT_UNAVAILABLE", "audit unavailable", 503)

    monkeypatch.setattr(admin_module, "audit", failed_audit)
    response = change(
        admin,
        "/configs/skill/" + row["id"],
        {"name": "MUST-NOT-SAVE", "expected_version": row["lock_version"]},
    )
    assert response.status_code == 503
    current = admin.get("/api/admin/configs/skill/" + row["id"]).json()["data"]
    assert current["name"] == row["name"] and current["revision_no"] == row["revision_no"]


async def test_verification_budget_stops_before_another_attempt(student):
    from study_trail.telemetry import verification_budget

    budget = {"used": 0, "limit": 1}
    token = verification_budget.set(budget)
    try:
        async with attempt({"owner_id": student["id"], "kind": "chat"}):
            pass
        with pytest.raises(core.Problem) as exc:
            async with attempt({"owner_id": student["id"], "kind": "chat"}):
                pytest.fail("The second external request must not be started")
        assert exc.value.code == "VERIFICATION_BUDGET_EXHAUSTED"
        assert budget["used"] == 1
    finally:
        verification_budget.reset(token)


def test_disabled_account_closes_sse_without_stopping_task(client, resource, admin, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    accepted = post(
        client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "stream"}
    ).json()["data"]
    reading = threading.Event()
    authenticating = core.authenticate

    def observed(conn, request, **kwargs):
        result = authenticating(conn, request, **kwargs)
        if request.url.path.endswith("/events"):
            reading.set()
        return result

    monkeypatch.setattr(core, "authenticate", observed)
    user = admin.get("/api/admin/users").json()["data"]["items"][0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.get, "/api/v1/tasks/" + accepted["task"]["id"] + "/events")
        assert reading.wait(3)
        disabled = change(
            admin,
            "/users/" + user["id"] + "/status",
            {"enabled": False, "expected_version": user["lock_version"]},
        )
        assert disabled.status_code == 200
        assert response.result(timeout=4).status_code == 200
    with db.engine.connect() as conn:
        assert db.one(conn, db.tasks, UUID(accepted["task"]["id"]))["status"] == "queued"
