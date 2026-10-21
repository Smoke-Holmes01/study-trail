from uuid import UUID, uuid4

import pytest

from study_trail import core as c
from study_trail import db
from study_trail.execution import claim, run_task
from study_trail.files import chunks, decode_text
from study_trail.providers import parse_search
from study_trail.workflow import atomic_commit, plan_validate

from .test_api import post


def payload():
    return {
        "title": "数学基础学习计划",
        "goal": "理解矩阵",
        "deadline": None,
        "stages": [
            {
                "id": str(uuid4()),
                "title": "矩阵入门",
                "goal": "会判断维度",
                "knowledge_points": ["矩阵维度"],
                "tasks": [
                    {
                        "id": str(uuid4()),
                        "title": "练习维度判断",
                        "estimated_minutes": 30,
                        "resource_source_ids": [],
                        "exercise_suggestions": ["判断两个矩阵能否相乘"],
                    }
                ],
            }
        ],
    }


class FakeGateway:
    def __init__(self, task):
        self.task = task

    async def structured(self, prompt, messages=None):
        if "判定当前学生请求意图" in prompt:
            action = (
                "plan_create" if "制定学习计划" in self.task["input_snapshot"]["content_text"] else "tutor"
            )
            return {"action": action, "target_hint": None, "deadline": None, "time_limit_minutes": None}
        if "covered" in prompt:
            return {"covered": True, "missing_query": ""}
        return payload()

    async def complete(self, messages, json_mode=False, on_chunk=None):
        text = "矩阵乘法需要左矩阵的列数等于右矩阵的行数。此处是模型解释。"
        if on_chunk:
            await on_chunk(text)
        return text

    async def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    async def rerank(self, query, docs):
        return list(range(min(6, len(docs))))

    async def search(self, query):
        return []


async def test_real_graph_plan_save_and_conversation_delete_preserves_plan(client, resource, monkeypatch):
    monkeypatch.setattr("study_trail.workflow.Gateway", FakeGateway)
    result = post(
        client,
        f"/conversations/{resource['conversation']['id']}/messages",
        {"content_text": "请制定学习计划"},
    ).json()["data"]
    claimed = claim("test")
    await run_task(claimed)
    snapshot = client.get("/api/v1/tasks/" + result["task"]["id"]).json()["data"]
    assert snapshot["status"] == "succeeded", snapshot
    assert snapshot["result"]["plan_mutation"]["committed"]
    ident = snapshot["result"]["plan_mutation"]["plan_id"]
    plan = client.get("/api/v1/plans/" + ident).json()["data"]
    assert plan["content"]["title"] == plan["name"]
    client.patch("/api/v1/plans/" + ident, json={"name": "我自己的计划"})
    renamed = client.get("/api/v1/plans/" + ident).json()["data"]
    assert renamed["content_version"] == 1
    assert renamed["content"]["title"] == "我自己的计划"
    client.delete("/api/v1/conversations/" + resource["conversation"]["id"])
    preserved = client.get("/api/v1/plans/" + ident).json()["data"]
    assert preserved["source_conversation_id"] is None


async def test_file_pipeline_full_index_and_failed_file_isolation(client, resource, monkeypatch):
    monkeypatch.setattr("study_trail.providers.Gateway", FakeGateway)
    kb = client.post("/api/v1/knowledge-bases", json={"name": "笔记"}).json()["data"]
    response = client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/files",
        files=[
            ("files", ("good.txt", "矩阵维度：行数乘列数。".encode(), "text/plain")),
            ("files", ("bad.txt", b"\xff", "text/plain")),
        ],
        headers={"Idempotency-Key": str(uuid4())},
    )
    items = response.json()["data"]["items"]
    for _ in range(2):
        await run_task(claim("test"))
    assert client.get("/api/v1/files/" + items[0]["file"]["id"]).json()["data"]["status"] == "ready"
    assert client.get("/api/v1/files/" + items[1]["file"]["id"]).json()["data"]["status"] == "failed"
    preview = client.get("/api/v1/files/" + items[0]["file"]["id"] + "/preview").json()["data"]
    assert "矩阵维度" in preview["blocks"][0]["text"]
    assert client.get("/api/v1/files/" + items[1]["file"]["id"] + "/preview").status_code == 409


async def test_plan_validation_one_repair_and_deadline_constraints():
    candidate = payload()
    candidate["deadline"] = "2026-12-01"
    state = {
        "plan": candidate,
        "target": None,
        "sources": [],
        "intent": {"deadline": None},
        "repair_used": False,
    }
    result = await plan_validate(state)
    assert result["errors"]
    with pytest.raises(c.Problem) as error:
        await plan_validate({**state, "repair_used": True})
    assert error.value.code == "PLAN_VALIDATION_FAILED"
    candidate["deadline"] = None
    candidate["stages"][0]["tasks"][0]["estimated_minutes"] = True
    assert (await plan_validate({**state, "plan": candidate}))["errors"]


async def test_concurrent_plan_version_commit_and_rename_preservation(client, resource):
    owner = UUID(resource["student"]["id"])
    agent = UUID(resource["agent"]["id"])
    value = payload()
    name = value.pop("title")
    with db.engine.begin() as conn:
        plan = db.insert(conn, db.learning_plans, owner_id=owner, agent_id=agent, name=name, content=value)
    tasks = []
    for i in range(2):
        conv = (
            resource["conversation"]["id"]
            if i == 0
            else client.post(f"/api/v1/agents/{agent}/conversations", json={}).json()["data"]["id"]
        )
        post(
            client,
            f"/conversations/{conv}/messages",
            {"content_text": "修改此计划", "target_plan_id": str(plan["id"]), "expected_plan_version": 1},
        )
        tasks.append(claim("test"))
    client.patch("/api/v1/plans/" + str(plan["id"]), json={"name": "重命名后的计划"})
    state = lambda t: {
        "task": t,
        "action": "plan_update",
        "sources": [],
        "answer": "候选",
        "plan": payload(),
        "exercises": [],
    }
    await atomic_commit(state(tasks[0]))
    with pytest.raises(c.Problem) as err:
        await atomic_commit(state(tasks[1]))
    assert err.value.code == "PLAN_VERSION_CONFLICT"
    saved = client.get("/api/v1/plans/" + str(plan["id"])).json()["data"]
    assert saved["content_version"] == 2
    assert saved["name"] == "重命名后的计划"


def test_strict_encoding_and_retraceable_chunks(tmp_path):
    for encoding in ["utf-8", "utf-8-sig", "utf-16", "gb18030"]:
        value, used = decode_text("矩阵学习".encode(encoding))
        assert value == "矩阵学习"
    text = "这是教材的一段内容。\n" * 400
    data = {"boundaries": [{"start": 0, "end": len(text), "page": 1}]}
    result = chunks(text, data)
    assert len(result) > 1
    for row in result:
        assert row["content_text"] == text[row["char_start"] : row["char_end"]]
        assert row["locator"]["page_start"] == 1


def test_exa_text_normalization_and_bad_urls():
    rows = parse_search(
        "Title: 资料标题\nURL: https://example.com/lesson\nHighlights: 实际搜索片段\n---\nTitle: 恶意\nURL: javascript:alert(1)\nHighlights: bad"
    )
    assert len(rows) == 1
    assert rows[0]["excerpt"] == "实际搜索片段"
