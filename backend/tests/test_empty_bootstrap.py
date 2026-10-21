from uuid import UUID, uuid4
import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa

from study_trail import db, platform
from study_trail.bootstrap import initialize
from study_trail.config import settings
from .test_management import admin as admin


@pytest.fixture
def empty_platform(monkeypatch):
    # Test-only truncation; the shared fixture already restricts this connection.
    assert db.engine.url.database == "study_trail_test"
    with db.engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE platform_configs, config_revisions, credential_versions, platform_bindings, migration_imports CASCADE")
        initialize(conn)
    monkeypatch.setattr(type(settings()), "models", lambda self: platform.catalog("model"))


def generation(conn):
    row = platform.create(conn, "model", {
        "stable_id": "admin-model", "name": "管理员模型", "enabled": True,
        "payload": {"model_kind": "generative", "api_model_id": "my-model",
                    "endpoint": "https://example.invalid/v1", "exposed": True,
                    "capabilities": {"supports_images": False, "max_images": 0, "max_image_bytes": 0,
                                     "input_token_budget": 32768, "max_output_tokens": 4096}},
        "secret_updates": {"api_key": "synthetic-test-secret"},
    }, None)
    return row


def test_empty_initialization_never_reads_external_service_files(empty_platform, monkeypatch, client, student):
    def forbidden(*args):
        raise AssertionError("External service files must not be imported")

    monkeypatch.setattr(type(settings()), "model_catalog", forbidden)
    monkeypatch.setattr(type(settings()), "file_mcp_servers", forbidden)
    with db.engine.begin() as conn:
        initialize(conn)
        initialize(conn)
        kinds = conn.execute(sa.select(db.platform_configs.c.kind)).scalars().all()
        assert set(kinds) == {"prompt", "skill", "limits"}
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.credential_versions)) == 0
        assert len(kinds) == 13  # Eight prompts, four skills, one limits profile.
    runpy.run_path(str(Path(__file__).parents[1] / "tools/check_installation.py"))
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "default_model_id": None}
    assert client.get("/api/v1/mcp/servers").json()["data"] == {"items": []}
    assert client.get("/api/v1/runtime-limits").json()["data"]["model_readiness"] == {
        "default_generation": False, "embedding": False, "rerank": False,
    }
    missing = client.post("/api/v1/agents", json={"name": "伙伴", "system_prompt": "辅助学习"})
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "REQUIRED_CONFIG_MISSING"
    assert "默认生成模型" in missing.json()["error"]["message"]


def test_generation_alone_allows_agents_and_chat_and_repeat_init_preserves_edits(empty_platform, client, student, admin):
    with db.engine.begin() as conn:
        row = generation(conn)
    bound = admin.patch("/api/admin/bindings/default_generation", json={
        "config_id": str(row["id"]), "confirm_switch": True, "expected_version": 0,
    }, headers={"Idempotency-Key": str(uuid4())})
    assert bound.status_code == 200, bound.text
    stale = admin.patch("/api/admin/bindings/default_generation", json={
        "config_id": str(row["id"]), "confirm_switch": True, "expected_version": 0,
    }, headers={"Idempotency-Key": str(uuid4())})
    assert stale.status_code == 409
    with db.engine.begin() as conn:
        initialize(conn)
        assert db.one(conn, db.platform_configs, row["id"])["current_revision_id"] == row["current_revision_id"]
        assert platform.bound(conn, "default_generation")[0]["id"] == row["id"]
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.credential_versions)) == 1
        frozen = platform.snapshot(conn)
        assert frozen["embedding"] is None and frozen["rerank"] is None
    agent = client.post("/api/v1/agents", json={"name": "伙伴", "system_prompt": "辅助学习"})
    assert agent.status_code == 201, agent.text
    conversation = client.post(f"/api/v1/agents/{agent.json()['data']['id']}/conversations", json={}).json()["data"]
    accepted = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content_text": "你好"}, headers={"Idempotency-Key": str(uuid4())})
    assert accepted.status_code == 202, accepted.text
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    upload = client.post(f"/api/v1/knowledge-bases/{kb['id']}/files", files=[("files", ("note.txt", b"study", "text/plain"))], headers={"Idempotency-Key": str(uuid4())})
    assert upload.status_code == 409
    assert "嵌入模型" in upload.json()["error"]["message"]
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.files)) == 0


def test_embedding_only_accepts_file_processing_without_generation(empty_platform, client, student):
    with db.engine.begin() as conn:
        row = platform.create(conn, "model", {"stable_id": "my-embedding", "name": "自定义嵌入", "enabled": True,
            "payload": {"model_kind": "embedding", "api_model_id": "custom-1024", "endpoint": "https://example.invalid/v1",
                "dimensions": 1024, "model_revision": "r1", "normalization_version": "dense-v1", "chunking_version": "v1", "capabilities": {}},
            "secret_updates": {"api_key": "synthetic-test-secret"}}, None)
        platform.bind(conn, "embedding", row)
    kb = client.post("/api/v1/knowledge-bases", json={"name": "资料"}).json()["data"]
    response = client.post(f"/api/v1/knowledge-bases/{kb['id']}/files", files=[("files", ("note.txt", b"study", "text/plain"))], headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 202, response.text
    with db.engine.connect() as conn:
        task = db.one(conn, db.tasks, UUID(response.json()["data"]["items"][0]["task"]["id"]))
        assert task["config_snapshot"]["embedding"]["api_model_id"] == "custom-1024"
        assert "model_id" not in task["config_snapshot"]
