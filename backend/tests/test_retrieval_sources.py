from contextlib import asynccontextmanager
from uuid import UUID, uuid4
import json

import pytest

from study_trail import db, workflow, mcp_tools, platform
from study_trail.builtin_search import configuration
from study_trail.execution import claim, run_task
from .test_workflow import FakeGateway, payload
from .test_api import post


@pytest.mark.parametrize("text", ["你好", "现在有了吗", "检查一下网络搜索可以使用吗"])
async def test_greetings_and_capability_questions_do_not_retrieve(client, resource, monkeypatch, text):
    class Gateway(FakeGateway):
        async def embed(self, texts):
            raise AssertionError("Must not retrieve")
        async def tool_turn(self, *args):
            raise AssertionError("Must not call external tools")
    monkeypatch.setattr(workflow, "Gateway", Gateway)
    async def forbidden(*args):
        raise AssertionError("Must not connect")
    monkeypatch.setattr(mcp_tools, "connect", forbidden)
    accepted = post(client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": text}).json()["data"]
    task = claim("test")
    task["config_snapshot"]["knowledge_base_ids"] = [str(uuid4())]
    task["config_snapshot"]["builtin_search"] = configuration()
    await run_task(task)
    result = client.get("/api/v1/tasks/" + accepted["task"]["id"]).json()["data"]
    assert result["status"] == "succeeded", result
    assert result["result"]["sources"] == []
    assert any("未调用" in item for item in result["result"]["execution_info"])
    if text != "你好":
        assert "未执行" in result["result"]["preview_text"]


def source():
    return {"id": str(uuid4()), "kind": "web", "title": "实际网页", "url": "https://example.com/lesson",
            "excerpt": "矩阵是二维数组", "file_id": None, "chunk_id": None, "locator": None, "status": "external"}


def test_only_actual_citations_and_plan_references_are_kept():
    first, second = source(), source()
    state = {"answer": f"矩阵有行和列[资料](source:{first['id']})", "sources": [first, second]}
    assert workflow.used_sources(state) == [first]
    assert workflow.used_sources({**state, "answer": "你好"}) == []
    plan = payload()
    plan["stages"][0]["tasks"][0]["resource_source_ids"] = [second["id"]]
    assert workflow.used_sources({**state, "answer": "计划", "plan": plan}) == [second]
    with pytest.raises(workflow.c.Problem):
        workflow.used_sources({**state, "answer": f"伪引用[资料](source:{uuid4()})"})


async def test_commit_persists_only_cited_sources_and_execution_facts(client, resource):
    accepted = post(client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "矩阵"}).json()["data"]
    task = claim("test")
    first, second = source(), source()
    state = {"task": task, "action": "tutor", "answer": f"矩阵是二维数组。[资料](source:{first['id']})", "sources": [first, second], "execution_info": ["内置搜索 web_search_exa：调用成功"]}
    await workflow.atomic_commit(state)
    messages = client.get(f"/api/v1/conversations/{resource['conversation']['id']}/messages").json()["data"]["items"]
    answer = next(m for m in messages if m["id"] == accepted["task"]["assistant_message_id"])
    assert [s["id"] for s in answer["sources"]] == [first["id"]]
    assert answer["execution_info"] == state["execution_info"]
    with db.engine.connect() as conn:
        assert not db.one(conn, db.sources, UUID(second["id"]))


@pytest.mark.parametrize("fail", [False, True])
async def test_builtin_search_works_without_admin_services_and_records_facts(monkeypatch, fail):
    captured = []
    @asynccontextmanager
    async def connect(server, timeout):
        captured.append(server)
        yield object()
    async def discover(session, ident, server, timeout):
        return [{"server_id": ident, "name": "web_search_exa", "description": "search", "input_schema": {"type":"object", "properties":{"query":{"type":"string"}}}}]
    async def invoke(*args):
        if fail:
            raise workflow.c.Problem("MCP_UNAVAILABLE", "网络搜索暂不可用", 503)
        return {"content":[{"type":"text", "text":"Title: 实际网页\nURL: https://example.com/lesson\nText: 矩阵是二维数组"}]}
    class Gateway:
        turn = 0
        def timeout(self, maximum): return maximum
        async def tool_turn(self, messages, functions):
            self.turn += 1
            return {"role":"assistant", "content":None, "tool_calls": [] if self.turn > 1 else [{"id":"call-1", "type":"function", "function":{"name":functions[0]["function"]["name"], "arguments":json.dumps({"query":"矩阵"})}}]}
    monkeypatch.setattr(mcp_tools, "connect", connect)
    monkeypatch.setattr(mcp_tools, "discover", discover)
    monkeypatch.setattr(mcp_tools, "invoke", invoke)
    monkeypatch.setattr(workflow, "publish", lambda *a, **kw: None)
    state = {"task":{"config_snapshot":{"mcp_servers":{}, "builtin_search":configuration(), "limits":dict(platform.DEFAULT_LIMITS), "input_token_budget":32768}}, "gateway":Gateway(), "sources":[], "degradation":[], "search_needed":True, "search_query":"矩阵", "context":[], "intent":{"retrieval_mode":"web"}}
    result = await workflow.web_search(state)
    assert len(captured) == 1 and captured[0]["allowed_tools"] == ["web_search_exa", "web_fetch_exa"]
    assert bool(result["sources"]) is not fail
    assert any("调用成功" in line for line in result["execution_info"]) is not fail
    if fail:
        assert any("调用失败" in line for line in result["execution_info"])
        assert result["degradation"]


async def test_knowledge_only_does_not_fall_back_to_web(monkeypatch):
    state = {"task":{}, "sources":[], "intent":{"retrieval_mode":"knowledge"}}
    assert (await workflow.coverage(state))["search_needed"] is False


async def test_model_knowledge_without_sources_does_not_automatically_search():
    state = {"task":{"input_snapshot":{"content_text":"解释矩阵"}}, "sources":[], "intent":{"retrieval_mode":"auto"}}
    assert (await workflow.coverage(state))["search_needed"] is False


def test_exa_fetch_structured_result_becomes_real_web_source():
    from study_trail.providers import parse_search_result
    result = parse_search_result({"structuredContent":{"results":[
        {"url":"https://example.com/lesson", "title":"网页标题", "text":"网页正文"},
        {"url":"javascript:alert(1)", "title":"非法地址", "text":"不应显示"},
    ]}})
    assert len(result) == 1
    assert result[0]["title"] == "网页标题" and result[0]["excerpt"] == "网页正文"


async def test_real_knowledge_retrieval_keeps_only_cited_chunk(client, resource, monkeypatch):
    import re
    from study_trail.files import chunks
    owner = UUID(resource["student"]["id"])
    kb = client.post("/api/v1/knowledge-bases", json={"name":"课程资料"}).json()["data"]
    text = "矩阵是由行和列组成的二维数组。" * 150
    with db.engine.begin() as conn:
        file = db.insert(conn, db.files, owner_id=owner, knowledge_base_id=UUID(kb["id"]), original_name="课程.txt", extension=".txt", media_type="text/plain", storage_key=workflow.c.store_bytes(text.encode()), sha256=b'a'*32, byte_size=len(text.encode()), status="ready", text_snapshot=text)
        for chunk in chunks(text, {"boundaries":[{"start":0,"end":len(text),"line":1}]}):
            db.insert(conn, db.document_chunks, owner_id=owner, knowledge_base_id=UUID(kb["id"]), file_id=file["id"], parse_version=1, embedding=[1.0]+[0.0]*1023, **chunk)
    assert client.patch("/api/v1/agents/"+resource["agent"]["id"],json={"knowledge_base_ids":[kb["id"]]}).status_code == 200
    class Gateway(FakeGateway):
        async def structured(self,prompt,messages=None):
            value=await super().structured(prompt,messages)
            if "判定当前学生请求意图" in prompt:
                value["retrieval_mode"]="knowledge"
            return value
        async def complete(self,messages,**kwargs):
            ids=re.findall(r'"id": "([0-9a-f-]{36})"',"\n".join(m["content"] for m in messages if isinstance(m["content"],str)))
            answer=f"矩阵有行和列。[课程](source:{ids[0]})"
            if kwargs.get("on_chunk"):
                await kwargs["on_chunk"](answer)
            return answer
    monkeypatch.setattr(workflow,"Gateway",Gateway)
    result=post(client,f"/conversations/{resource['conversation']['id']}/messages",{"content_text":"根据课程解释矩阵"}).json()["data"]
    await run_task(claim("test"))
    task=client.get("/api/v1/tasks/"+result["task"]["id"]).json()["data"]
    assert task["status"]=="succeeded",task
    assert len(task["result"]["sources"])==1
    assert task["result"]["sources"][0]["file_id"]==str(file["id"])
    assert "本次已检索知识库" in task["result"]["execution_info"]


def test_builtin_is_independent_of_disabled_legacy_exa_selection(client, resource, monkeypatch):
    import sqlalchemy as sa
    monkeypatch.setattr("study_trail.builtin_search.configuration", configuration)
    with db.engine.begin() as conn:
        legacy = conn.execute(sa.select(db.platform_configs).where(db.platform_configs.c.kind=="mcp",db.platform_configs.c.stable_id=="exa")).mappings().one()
        db.update(conn,db.platform_configs,legacy["id"],enabled=False)
        agent = db.one(conn,db.agents,UUID(resource["agent"]["id"]))
        snapshot = workflow.c.agent_config(conn,{**agent,"mcp_server_ids":["exa"]})
        assert snapshot["builtin_search"]["allowed_tools"] == ["web_search_exa","web_fetch_exa"]
        assert snapshot["mcp_servers"] == {}
