import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError

from study_trail import core, db, mcp_tools, platform, skills, workflow
from study_trail.bootstrap import initialize, refresh_builtin_guide
from study_trail.config import settings
from study_trail.schemas import PlanPayload
from .test_management import admin as admin
from .test_management import change, configs
from .test_workflow import payload
from .test_capabilities import ToolGateway


@pytest.mark.parametrize("constraints", [None, [], "invalid", {"foreign": {}}, {"web_search_exa": {"type": "invalid"}}])
def test_mcp_constraints_return_field_errors_without_saving(admin, constraints):
    row = configs(admin, "mcp")[0]
    proposed = {**row["payload"], "parameter_schemas": constraints}
    response = change(admin, "/configs/mcp/" + row["id"], {
        "expected_version": row["lock_version"], "payload": proposed,
    })
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field_errors"][0]["field"] == "payload.parameter_schemas"
    current = admin.get("/api/admin/configs/mcp/" + row["id"]).json()["data"]
    assert current["current_revision_id"] == row["current_revision_id"]
    assert current["payload"] == row["payload"]


def test_fixed_defaults_ignore_legacy_flags_and_reject_new_conflicts(admin, client, student):
    rows = configs(admin, "skill")
    with db.engine.begin() as conn:
        for row in rows:
            rev = db.one(conn, db.config_revisions, UUID(row["current_revision_id"]))
            legacy = {**rev["payload"], "default_enabled": row["stable_id"] == "study-trail-guide"}
            revision_id = uuid4()
            conn.execute(db.config_revisions.insert().values(
                id=revision_id, config_id=rev["config_id"], revision_no=rev["revision_no"] + 1,
                payload=legacy, credential_version_ids={}, content_hash=platform.digest([legacy, {}]),
            ))
            db.update(conn, db.platform_configs, UUID(row["id"]), current_revision_id=revision_id)
    catalog = client.get("/api/v1/skills").json()["data"]["items"]
    assert {s["id"] for s in catalog if s["default_enabled"]} == skills.DEFAULT_SKILL_IDS
    created = client.post("/api/v1/agents", json={"name": "默认技能", "system_prompt": "帮助自主学习"})
    assert created.status_code == 201
    assert set(created.json()["data"]["skill_ids"]) == skills.DEFAULT_SKILL_IDS
    guide = next(row for row in rows if row["stable_id"] == "study-trail-guide")
    bad = change(admin, "/configs/skill/" + guide["id"], {
        "expected_version": guide["lock_version"], "payload": {**guide["payload"], "default_enabled": True},
    })
    assert bad.status_code == 422
    explain = next(row for row in rows if row["stable_id"] == "explain")
    assert change(admin, "/configs/skill/" + explain["id"] + "/status", {
        "enabled": False, "expected_version": explain["lock_version"],
    }).status_code == 200
    second = client.post("/api/v1/agents", json={"name": "停用后", "system_prompt": "帮助自主学习"}).json()["data"]
    assert set(second["skill_ids"]) == {"study-plan", "practice"}
    assert set(client.get("/api/v1/agents/" + created.json()["data"]["id"]).json()["data"]["skill_ids"]) == skills.DEFAULT_SKILL_IDS


@pytest.mark.parametrize("invalid", ["duplicate_sources", "empty_knowledge_points"])
def test_plan_contract_rejects_ambiguous_structure(invalid):
    value = payload()
    if invalid == "duplicate_sources":
        value["stages"][0]["tasks"][0]["resource_source_ids"] = [
            "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000001"
        ]
    else:
        value["stages"][0]["knowledge_points"] = []
    with pytest.raises(ValidationError):
        PlanPayload.model_validate(value)
    assert PlanPayload.model_json_schema()["$defs"]["PlanTask"]["properties"]["resource_source_ids"]["uniqueItems"] is True


def test_explicit_guide_refresh_is_versioned_preserves_status_and_repeatable(admin):
    actor = UUID(admin.get("/api/admin/me").json()["data"]["id"])
    row = next(r for r in configs(admin, "skill") if r["stable_id"] == "study-trail-guide")
    with db.engine.begin() as conn:
        current = db.one(conn, db.platform_configs, UUID(row["id"]))
        customized = platform.revision(conn, current, {**row["payload"], "body": "旧的自定义指南"}, None, actor)
        db.update(conn, db.platform_configs, current["id"], enabled=False)
    with db.engine.begin() as conn:
        refresh_builtin_guide(conn, actor)
        initialize(conn)
        refreshed = db.one(conn, db.platform_configs, UUID(row["id"]))
        revision_id = refreshed["current_revision_id"]
        assert not refreshed["enabled"]
        assert db.one(conn, db.config_revisions, customized["id"])["payload"]["body"] == "旧的自定义指南"
        assert "数据库为唯一来源" in db.one(conn, db.config_revisions, revision_id)["payload"]["body"]
        refresh_builtin_guide(conn, actor)
        initialize(conn)
        assert db.one(conn, db.platform_configs, refreshed["id"])["current_revision_id"] == revision_id


@pytest.mark.parametrize("file_fp,chunk_fp,success", [("a" * 64, None, True), (None, None, False), ("a" * 64, "b" * 64, False)])
def test_0006_requires_verified_identity(monkeypatch, file_fp, chunk_fp, success):
    path = Path(__file__).parents[1] / "alembic/versions/0006_chunk_fingerprint.py"
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TEMP TABLE files(id int,owner_id int,index_fingerprint varchar(64)) ON COMMIT DROP")
        conn.exec_driver_sql("CREATE TEMP TABLE document_chunks(file_id int,owner_id int,index_fingerprint varchar(64)) ON COMMIT DROP")
        conn.execute(sa.text("INSERT INTO files VALUES(1,1,:fp)"), {"fp": file_fp})
        conn.execute(sa.text("INSERT INTO document_chunks VALUES(1,1,:fp)"), {"fp": chunk_fp})
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        if success:
            migration.upgrade()
            assert conn.exec_driver_sql("SELECT index_fingerprint FROM document_chunks").scalar() == file_fp
            assert conn.exec_driver_sql("SELECT attnotnull FROM pg_attribute WHERE attrelid='document_chunks'::regclass AND attname='index_fingerprint'").scalar()
        else:
            with pytest.raises(RuntimeError, match="Index identity"):
                migration.upgrade()


async def test_mcp_result_limit_degrades_but_context_limit_terminates(monkeypatch):
    server = settings().mcp_servers()["exa"]
    tool = {"server_id": "exa", "name": "web_search_exa", "description": "search", "input_schema": {"type": "object"}}
    @asynccontextmanager
    async def connect(*args):
        yield object()
    async def discover(*args):
        return [tool]
    async def too_large(*args):
        raise core.Problem("MCP_RESULT_LIMIT", "工具结果超过容量", 409)
    monkeypatch.setattr(mcp_tools, "connect", connect)
    monkeypatch.setattr(mcp_tools, "discover", discover)
    monkeypatch.setattr(mcp_tools, "invoke", too_large)
    monkeypatch.setattr(workflow, "publish", lambda *a: None)
    state = {"task": {"config_snapshot": {"mcp_servers": {"exa": server}, "input_token_budget": 32768, "limits": dict(platform.DEFAULT_LIMITS)}},
             "gateway": ToolGateway([tool]), "sources": [], "degradation": [], "search_needed": True, "search_query": "矩阵", "context": []}
    result = await workflow.web_search(state)
    assert result["sources"] == []
    assert "容量" in "".join(result["degradation"])
    async def overflow(*args):
        raise core.Problem("CONTEXT_LIMIT_EXCEEDED", "整体上下文超过容量", 409)
    state["gateway"].tool_turn = overflow
    with pytest.raises(core.Problem) as error:
        await workflow.web_search(state)
    assert error.value.code == "CONTEXT_LIMIT_EXCEEDED"
