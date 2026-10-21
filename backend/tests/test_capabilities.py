import asyncio
import copy
import json
import shutil
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

from study_trail import core, db, mcp_tools, skills, workflow, platform
import sqlalchemy as sa
from study_trail.config import Settings, settings
from study_trail.providers import Gateway

from .test_api import post
from .test_workflow import FakeGateway

REAL_MODELS = Settings.models
CONFIG_ROOT = settings().config_root


@pytest.fixture
def config_copy(tmp_path, monkeypatch):
    root = tmp_path / "config"
    shutil.copytree(CONFIG_ROOT, root)
    monkeypatch.setattr(settings(), "config_root", root)
    return root


def test_model_catalog_reload_provider_mapping_and_env_refs(config_copy, monkeypatch):
    monkeypatch.setattr(Settings, "models", REAL_MODELS)
    monkeypatch.setenv("SECOND_KEY", "private-test-key")
    config = settings().read_config("model.json")
    config["providers"]["second"] = {"base_url": "https://second.invalid/v1", "api_key": "{env:SECOND_KEY}"}
    config["models"]["alias"] = {
        **config["models"][config["default_model_id"]],
        "provider_id": "second",
        "api_model_id": "upstream-model",
    }
    config["default_model_id"] = "alias"
    (config_copy / "model.json").write_text(json.dumps(config), encoding="utf-8")
    # File changes never mutate runtime configuration after initialization.
    assert core.model_config()["model_id"] != "alias"
    with db.engine.begin() as conn:
        platform.lock(conn)
        row = platform.create(conn, "model", {"stable_id": "alias", "name": "alias", "enabled": True,
            "payload": {"model_kind": "generative", "api_model_id": "upstream-model", "endpoint": "https://second.invalid/v1",
                "capabilities": {"supports_images": False, "max_images": 0, "max_image_bytes": 0,
                    "input_token_budget": 32768, "max_output_tokens": 4096}},
            "secret_updates": {"api_key": "private-test-key"}}, None)
        platform.bind(conn, "default_generation", row)
    model = core.model_config()
    assert model["model_id"] == "alias"
    assert model["api_model_id"] == "upstream-model"
    assert "private-test-key" not in json.dumps(model)
    url, headers = Gateway({"config_snapshot": model}).connection()
    assert url == "https://second.invalid/v1"
    assert headers["Authorization"] == "Bearer private-test-key"
    monkeypatch.setenv("SECOND_KEY", "rotated-env-key")
    assert Gateway({"config_snapshot": model}).connection()[1]["Authorization"] == "Bearer private-test-key"


def test_catalogs_defaults_switch_validation_and_no_definition_writes(client, student):
    created = client.post("/api/v1/agents", json={"name": "技能伙伴", "system_prompt": "辅导"}).json()["data"]
    assert created["mcp_server_ids"] == ["exa"]
    assert set(created["skill_ids"]) == {"explain", "practice", "study-plan"}
    empty = client.patch("/api/v1/agents/" + created["id"], json={"mcp_server_ids": [], "skill_ids": []})
    assert empty.json()["data"]["skill_ids"] == []
    assert empty.json()["data"]["config_version"] == 2
    for ids in (["unknown"], ["explain", "explain"]):
        assert client.patch("/api/v1/agents/" + created["id"], json={"skill_ids": ids}).status_code == 422
    catalog = client.get("/api/v1/skills").json()["data"]["items"]
    assert len(catalog) == 4
    assert all("body" not in item for item in catalog)
    servers = client.get("/api/v1/mcp/servers").json()["data"]["items"]
    assert "headers" not in servers[0] and "url" not in servers[0]
    assert client.post("/api/v1/skills", json={"name": "injected"}).status_code == 405
    assert client.post("/api/v1/mcp/servers", json={"url": "https://arbitrary.invalid"}).status_code == 405
    assert client.patch("/api/v1/agents/" + created["id"], json={"mcp_config": {}}).status_code == 422


def test_skill_snapshot_label_reload_retry_and_disabled_selection(client, resource, config_copy):
    path = f"/conversations/{resource['conversation']['id']}/messages"
    accepted = post(client, path, {"content_text": "解释矩阵", "skill_id": "explain"}).json()["data"]
    label = accepted["user_message"]["skill"]
    assert label["id"] == "explain"
    ident = UUID(accepted["task"]["id"])
    with db.engine.begin() as conn:
        before = copy.deepcopy(db.one(conn, db.tasks, ident)["config_snapshot"])
    skill_path = config_copy / "skill/explain/SKILL.md"
    skill_path.write_text(skill_path.read_text(encoding="utf-8") + "\n新技能版本标记。\n", encoding="utf-8")
    assert skills.catalog()["explain"]["version"] == before["skill"]["version"]
    with db.engine.begin() as conn:
        platform.lock(conn)
        row = conn.execute(sa.select(db.platform_configs).where(db.platform_configs.c.kind == "skill", db.platform_configs.c.stable_id == "explain")).mappings().one()
        rev = db.one(conn, db.config_revisions, row["current_revision_id"])
        platform.revision(conn, row, {**rev["payload"], "body": rev["payload"]["body"] + "\nnew revision"}, None, None)
    assert skills.catalog()["explain"]["version"] != before["skill"]["version"]
    with db.engine.begin() as conn:
        assert db.one(conn, db.tasks, ident)["config_snapshot"] == before
    history = client.get("/api/v1/conversations/" + resource["conversation"]["id"] + "/messages").json()[
        "data"
    ]["items"]
    assert history[0]["skill"]["id"] == "explain"
    assert post(client, "/tasks/" + str(ident) + "/stop", {}).status_code == 200
    retried = post(
        client, "/messages/" + accepted["user_message"]["id"] + "/retry", {"previous_task_id": str(ident)}
    ).json()["data"]
    assert retried["task"]["request_input"]["skill_id"] == "explain"
    with db.engine.begin() as conn:
        current = db.one(conn, db.tasks, UUID(retried["task"]["id"]))
        assert current["config_snapshot"]["skill"]["version"] != before["skill"]["version"]
    post(client, "/tasks/" + retried["task"]["id"] + "/stop", {})
    client.patch("/api/v1/agents/" + resource["agent"]["id"], json={"skill_ids": []})
    assert post(client, path, {"content_text": "矩阵", "skill_id": "explain"}).status_code == 409


def test_only_selected_skill_is_loaded_and_bad_skills_are_isolated(config_copy):
    bad = config_copy / "skill/bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: bad\ndescription: [wrong]\n---\ntext", encoding="utf-8")
    assert set(skills.catalog()) == {"explain", "practice", "study-plan", "study-trail-guide"}
    assert "bad" not in skills.file_catalog()


def test_credentials_cannot_enter_config_snapshots_as_literals(config_copy):
    data = settings().read_config("model.json")
    data["providers"]["generation"]["api_key"] = "literal-secret"
    (config_copy / "model.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(core.Problem) as invalid:
        settings().model_catalog()
    assert invalid.value.code == "CONFIG_INVALID"
    data = settings().read_config("mcp_config.json")
    data["mcpServers"]["exa"]["headers"]["x-api-key"] = "literal-secret"
    (config_copy / "mcp_config.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(core.Problem):
        settings().file_mcp_servers()


def test_environment_credential_rotation_and_removal(tmp_path, monkeypatch):
    from study_trail import config

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("GENERATION_API_KEY", raising=False)
    cfg = Settings(generation_api_key="cached-old-key")
    env_file.write_text("GENERATION_API_KEY=new-key\n", encoding="utf-8")
    assert cfg.resolve("{env:GENERATION_API_KEY}") == "new-key"
    env_file.write_text("", encoding="utf-8")
    assert cfg.resolve("{env:GENERATION_API_KEY}") == ""


def test_ownership_and_csrf_protect_tool_calls(client, resource):
    from .conftest import login

    original = resource["agent"]["id"]
    login(client)
    assert client.get(f"/api/v1/agents/{original}/mcp/tools").status_code == 404
    assert (
        client.post(
            f"/api/v1/agents/{original}/mcp/tools/call",
            json={"server_id": "exa", "tool_name": "web_search_exa", "arguments": {"query": "math"}},
        ).status_code
        == 404
    )
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/mcp/servers/exa/check").status_code == 403


class ToolGateway:
    def __init__(self, tools, repeat=False):
        self.tools = tools
        self.turns = 0
        self.repeat = repeat

    def timeout(self, maximum):
        return maximum

    async def tool_turn(self, messages, functions):
        self.turns += 1
        if self.turns > 1 and not self.repeat:
            return {"role": "assistant", "content": "", "tool_calls": []}
        assert any(msg["role"] == "tool" for msg in messages) if self.turns > 1 else True
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{self.turns}-{i}",
                    "type": "function",
                    "function": {
                        "name": functions[0]["function"]["name"],
                        "arguments": json.dumps({"query": f"矩阵 {self.turns}-{i}"}),
                    },
                }
                for i in range(2 if self.repeat else 1)
            ],
        }


async def test_graph_automatic_mcp_sources_limits_and_off_switch(monkeypatch):
    server = settings().mcp_servers()["exa"]
    tool = {
        "server_id": "exa",
        "name": "web_search_exa",
        "description": "search",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    calls = []

    @asynccontextmanager
    async def connect(*args):
        yield object()

    async def discover(*args):
        return [tool]

    async def invoke(session, cfg, definition, arguments, timeout):
        calls.append(arguments)
        return {
            "content": [
                {"type": "text", "text": "Title: 矩阵\nURL: https://example.com/math\nText: 矩阵有行和列。"}
            ]
        }

    monkeypatch.setattr(mcp_tools, "connect", connect)
    monkeypatch.setattr(mcp_tools, "discover", discover)
    monkeypatch.setattr(mcp_tools, "invoke", invoke)
    monkeypatch.setattr(workflow, "publish", lambda *a, **kw: None)
    state = {
        "task": {"config_snapshot": {"mcp_servers": {"exa": server}, "input_token_budget": 32768, "limits": dict(platform.DEFAULT_LIMITS)}},
        "gateway": ToolGateway([tool]),
        "sources": [],
        "degradation": [],
        "search_needed": True,
        "search_query": "矩阵",
        "context": [{"role": "user", "content": "解释矩阵"}],
    }
    result = await workflow.web_search(state)
    assert len(calls) == 1
    assert result["sources"][0]["url"] == "https://example.com/math"
    state["gateway"] = ToolGateway([tool], repeat=True)
    calls.clear()
    result = await workflow.web_search(state)
    assert len(calls) == 6
    assert state["gateway"].turns == 3
    assert len(result["sources"]) == 1
    assert "上限" in "".join(result["degradation"])
    state["task"]["config_snapshot"]["mcp_servers"] = {}
    calls.clear()
    assert "未启用" in "".join((await workflow.web_search(state))["degradation"])
    assert calls == []


async def test_cancellation_propagates_and_cleanup_runs(monkeypatch):
    closed = []
    server = settings().mcp_servers()["exa"]

    @asynccontextmanager
    async def connect(*args):
        try:
            yield object()
        finally:
            closed.append(True)

    async def discover(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(mcp_tools, "connect", connect)
    monkeypatch.setattr(mcp_tools, "discover", discover)
    monkeypatch.setattr(workflow, "publish", lambda *a, **kw: None)
    state = {
        "task": {"config_snapshot": {"mcp_servers": {"exa": server}, "input_token_budget": 32768, "limits": dict(platform.DEFAULT_LIMITS)}},
        "gateway": ToolGateway([]),
        "sources": [],
        "degradation": [],
        "search_needed": True,
        "search_query": "矩阵",
        "context": [],
    }
    with pytest.raises(asyncio.CancelledError):
        await workflow.web_search(state)
    assert closed == [True]


SERVER_SOURCE = """
import asyncio
import sys
from mcp.server.mcpserver import MCPServer
app = MCPServer("study-trail-test")
@app.tool()
def lookup(query: str) -> str:
    return "Title: Lesson\\nURL: https://example.com/lesson\\nText: " + query
@app.tool()
def not_allowed(query: str) -> str:
    return "not exposed"
@app.tool()
async def slow(query: str) -> str:
    await asyncio.sleep(10)
    return query
@app.tool()
def broken(query: str) -> str:
    raise ValueError("private upstream detail")
if len(sys.argv) > 1:
    app.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
else:
    app.run()
"""


@pytest.mark.parametrize("transport", ["stdio", "streamable_http"])
async def test_real_sdk_transports_discovery_schema_calls_timeout_cleanup(tmp_path, transport, student):
    script = tmp_path / "server.py"
    script.write_text(SERVER_SOURCE, encoding="utf-8")
    server = {
        **settings().mcp_servers()["exa"],
        "name": "fixture",
        "_task": {"kind": "chat", "owner_id": student["id"]},
        "transport": transport,
        "allowed_tools": ["lookup", "slow", "broken"],
        "headers": {},
        "timeout_seconds": 2,
    }
    process = None
    if transport == "stdio":
        server.update(command=sys.executable, args=[str(script)], env={}, cwd=None, url=None)
    else:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server["url"] = f"http://127.0.0.1:{port}/mcp"
        process = subprocess.Popen(
            [sys.executable, str(script), str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for _ in range(60):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if process.poll() is not None:
                pytest.fail("fixture server did not start")
            await asyncio.sleep(0.1)
    try:
        async with mcp_tools.connect(server) as session:
            tools = await mcp_tools.discover(session, "fixture", server)
            assert {t["name"] for t in tools} == {"lookup", "slow", "broken"}
            lookup = next(t for t in tools if t["name"] == "lookup")
            result = await mcp_tools.invoke(session, server, lookup, {"query": "matrix"})
            assert "matrix" in mcp_tools.text_result(result)
            with pytest.raises(core.Problem) as invalid:
                await mcp_tools.invoke(session, server, lookup, {"query": 12})
            assert invalid.value.code == "MCP_ARGUMENTS_INVALID"
            with pytest.raises(core.Problem) as broken:
                await mcp_tools.invoke(
                    session, server, next(t for t in tools if t["name"] == "broken"), {"query": "x"}
                )
            assert broken.value.code == "MCP_TOOL_FAILED"
            with pytest.raises(TimeoutError):
                await mcp_tools.invoke(
                    session, server, next(t for t in tools if t["name"] == "slow"), {"query": "x"}, 0.1
                )
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "skill_id,action", [("explain", "tutor"), ("study-plan", "plan_create"), ("practice", "exercise")]
)
async def test_three_skills_full_graph(client, resource, monkeypatch, skill_id, action):
    from study_trail.execution import claim, run_task
    from .test_workflow import payload

    class SkillGateway(FakeGateway):
        async def structured(self, prompt, messages=None):
            if "判定当前学生请求意图" in prompt:
                assert json.dumps(self.task["config_snapshot"]["skill"]["body"], ensure_ascii=False) in prompt
                return {"action": action, "target_hint": None, "deadline": None, "time_limit_minutes": None}
            if '"exercises"' in prompt:
                assert any(
                    self.task["config_snapshot"]["skill"]["body"] in str(msg.get("content"))
                    for msg in messages
                )
                return {
                    "exercises": [
                        {
                            "question_text": "计算 2 × 3。",
                            "provenance": "generated",
                            "source_ids": [],
                            "known_answer_snapshot": None,
                        }
                    ]
                }
            if "covered" in prompt:
                return {"covered": True, "missing_query": ""}
            return payload()

        async def complete(self, messages, **kwargs):
            assert any(
                self.task["config_snapshot"]["skill"]["body"] in str(msg.get("content")) for msg in messages
            )
            return await super().complete(messages, **kwargs)

    monkeypatch.setattr(workflow, "Gateway", SkillGateway)
    client.patch("/api/v1/agents/" + resource["agent"]["id"], json={"mcp_server_ids": []})
    result = post(
        client,
        f"/conversations/{resource['conversation']['id']}/messages",
        {"content_text": "矩阵基础", "skill_id": skill_id},
    ).json()["data"]
    await run_task(claim("skill-e2e"))
    task = client.get("/api/v1/tasks/" + result["task"]["id"]).json()["data"]
    assert task["status"] == "succeeded", task
    assert task["action"] == action
    if action == "plan_create":
        assert task["result"]["plan_mutation"]["committed"]
    if action == "exercise":
        assert task["result"]["exercise_ids"]
