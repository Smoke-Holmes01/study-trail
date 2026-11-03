"""Deterministic v0.1.0 collaboration; no external model or tool connections."""

import copy
import json
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
import sqlalchemy as sa

from study_trail import core as c, db, workflow, platform
from study_trail.execution import claim, run_task
from study_trail.orchestration import ROLE_GRAPHS, role_task, validate_dispatch
from .test_api import post
from .test_workflow import FakeGateway


class CollaborationGateway(FakeGateway):
    calls = []
    fail_summary = False
    fail_role = None

    async def structured(self, prompt, messages=None):
        if "判定当前学生请求意图" in prompt:
            return {"action": "plan_create", "target_hint": None, "deadline": None,
                    "time_limit_minutes": None, "retrieval_mode": "none",
                    "dispatch": [
                        {"step_id": "plan", "role": "plan", "purpose": "制定矩阵计划", "depends_on": [], "target_ids": []},
                        {"step_id": "tutor", "role": "tutor", "purpose": "讲解矩阵", "depends_on": [], "target_ids": []},
                        {"step_id": "practice", "role": "exercise", "purpose": "根据计划出题", "depends_on": ["plan"], "target_ids": []},
                    ]}
        if "为学生生成练习题" in prompt:
            self.calls.append("exercise")
            if self.fail_role == "exercise":
                raise c.Problem("MODEL_UNAVAILABLE", "合成练习失败", 503)
            return {"exercises": [{"question_text": "矩阵有几行几列？", "provenance": "generated",
                                   "source_ids": [], "known_answer_snapshot": None}]}
        return await super().structured(prompt, messages)

    async def complete(self, messages, json_mode=False, on_chunk=None):
        role = "role" if self.task.get("role_run_id") else "summary"
        self.calls.append(role)
        if role == "summary" and self.fail_summary:
            raise c.Problem("MODEL_UNAVAILABLE", "合成汇总失败", 503)
        return await super().complete(messages, json_mode, on_chunk)


@pytest.fixture
def gateway(monkeypatch):
    CollaborationGateway.calls = []
    CollaborationGateway.fail_summary = False
    CollaborationGateway.fail_role = None
    monkeypatch.setattr(workflow, "Gateway", CollaborationGateway)
    return CollaborationGateway


async def request(client, resource):
    response = post(client, f"/conversations/{resource['conversation']['id']}/messages",
                    {"content_text": "请制定矩阵学习计划，讲解矩阵，并根据计划出题"})
    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    task = claim("collaboration-test")
    await run_task(task)
    return client.get("/api/v1/tasks/" + accepted["task"]["id"]).json()["data"]


async def test_real_private_graphs_commit_plan_and_exercises_together(client, resource, gateway):
    assert len({id(graph) for graph in ROLE_GRAPHS.values()}) == 5
    result = await request(client, resource)
    assert result["status"] == "succeeded", result
    assert [r["role"] for r in result["role_runs"]] == ["plan", "tutor", "exercise"]
    assert all(r["status"] == "succeeded" for r in result["role_runs"])
    assert result["result"]["plan_mutation"]["committed"] is True
    assert len(result["result"]["exercise_ids"]) == 1
    assert "矩阵有几行几列？" in result["result"]["preview_text"]
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.learning_plan_revisions)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.exercise_revisions)) == 1
    assert gateway.calls[-1] == "summary"


@pytest.mark.parametrize("failure", ["summary", "exercise"])
async def test_late_failure_never_persists_partial_learning_content(client, resource, gateway, failure):
    gateway.fail_summary = failure == "summary"
    gateway.fail_role = "exercise" if failure == "exercise" else None
    result = await request(client, resource)
    assert result["status"] == "failed", result
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.learning_plans)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.exercises)) == 0
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.message_sources)) == 0
    assert result["result"]["plan_mutation"] is None


def test_role_state_filters_capabilities_without_mutating_parent(resource):
    parent = {"config_snapshot": {"system_prompt": "个人指令", "mcp_servers": {"one": {}, "two": {}},
        "skill": {"id": "practice", "body": "技能正文"}, "roles": {
            "plan": {"body": "制定计划", "allowed_mcp_ids": ["one"], "allowed_skill_ids": ["study-plan"]},
            "exercise": {"body": "出题", "allowed_mcp_ids": ["two"], "allowed_skill_ids": ["practice"]}}}}
    original = copy.deepcopy(parent)
    first, second = role_task(parent, "plan"), role_task(parent, "exercise")
    assert list(first["config_snapshot"]["mcp_servers"]) == ["one"]
    assert "skill" not in first["config_snapshot"]
    assert second["config_snapshot"]["skill"]["id"] == "practice"
    assert parent == original


@pytest.mark.parametrize("steps", [
    [{"step_id": "a", "role": "invented"}],
    [{"step_id": "a", "role": "plan", "depends_on": ["later"]}],
    [{"step_id": "a", "role": "tutor"}, {"step_id": "b", "role": "tutor"}],
    [{"step_id": "a", "role": "plan", "target_ids": ["another-users-plan"]}],
])
def test_dispatch_rejects_illegal_roles_dependencies_and_targets(resource, steps):
    with pytest.raises(ValueError):
        validate_dispatch(steps, "plan_create", {"input_snapshot": {}})


async def test_explanation_uses_frozen_revision_after_personal_edit(client, resource, monkeypatch):
    agent, conversation = resource["agent"]["id"], resource["conversation"]["id"]
    with db.engine.begin() as conn:
        e = db.insert(conn, db.exercises, owner_id=UUID(resource["student"]["id"]), agent_id=UUID(agent),
                      position=0, question_text="旧题目", provenance="generated", source_ids=[])
        from study_trail.learning_content import record_exercise
        e = record_exercise(conn, e)
        from study_trail.api import accept_chat
        from study_trail.schemas import Send
        data, _, _ = accept_chat(conn, e["owner_id"], UUID(conversation), Send(content_text="解析旧题目"), answer=e)
        frozen = db.one(conn, db.tasks, UUID(str(data["task"]["id"])))
        db.update(conn, db.exercises, e["id"], question_text="新题目", content_version=2)
    assert frozen["input_snapshot"]["exercise_snapshot"]["question_text"] == "旧题目"
    observed = []
    class FrozenGateway(FakeGateway):
        async def complete(self, messages, json_mode=False, on_chunk=None):
            if self.task.get("role_run_id"):
                observed.append(messages)
            return await super().complete(messages, json_mode, on_chunk)
    monkeypatch.setattr(workflow, "Gateway", FrozenGateway)
    await run_task(claim("freeze-test"))
    text = str(observed)
    assert "旧题目" in text
    assert "新题目" not in text


async def test_five_roles_use_one_parent_and_private_graphs(client, resource, monkeypatch):
    with db.engine.begin() as conn:
        owner = UUID(resource["student"]["id"])
        e = db.insert(conn, db.exercises, owner_id=owner, agent_id=UUID(resource["agent"]["id"]),
                      position=0, question_text="请评价这个旧题的答案", provenance="generated", source_ids=[])
        from study_trail.learning_content import record_exercise
        e = record_exercise(conn, e)
    class FiveRoleGateway(CollaborationGateway):
        async def structured(self, prompt, messages=None):
            result = await super().structured(prompt, messages)
            if "判定当前学生请求意图" in prompt:
                result["dispatch"] += [
                    {"step_id": "explain", "role": "explanation", "purpose": "解析旧题", "depends_on": [], "target_ids": [str(e["id"])]},
                    {"step_id": "feedback", "role": "feedback", "purpose": "评价旧题作答", "depends_on": ["explain"], "target_ids": [str(e["id"])]},
                ]
            return result
    FiveRoleGateway.fail_role, FiveRoleGateway.fail_summary = None, False
    monkeypatch.setattr(workflow, "Gateway", FiveRoleGateway)
    response = post(client, f"/conversations/{resource['conversation']['id']}/messages", {
        "content_text": f"请制定计划、讲解、出题，并解析旧题{e['id']}，评价我的作答：矩阵有两行。"})
    accepted = response.json()["data"]
    await run_task(claim("five-role-test"))
    result = client.get("/api/v1/tasks/" + accepted["task"]["id"]).json()["data"]
    assert result["status"] == "succeeded", result
    assert len(result["role_runs"]) == 5
    assert {r["role"] for r in result["role_runs"]} == set(ROLE_GRAPHS)
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.tasks).where(db.tasks.c.kind == "chat")) == 1
    assert all(set(r) == {"role", "step_no", "status", "phase", "started_at", "finished_at", "error_code", "degradation"}
               for r in result["role_runs"])


async def test_tool_budget_and_cache_belong_to_parent_round(monkeypatch):
    from study_trail import mcp_tools
    connections, invocations = [], []
    @asynccontextmanager
    async def connect(*args):
        connections.append(True)
        yield object()
    async def discover(*args):
        return [{"server_id": "mock", "name": "lookup", "description": "合成工具", "input_schema": {"type": "object"}}]
    async def invoke(*args):
        invocations.append(True)
        return {"content": [{"type": "text", "text": "合成结果"}]}
    class ToolGateway:
        def timeout(self, maximum):
            return maximum
        async def tool_turn(self, messages, functions):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "one", "type": "function", "function": {
                "name": functions[0]["function"]["name"], "arguments": json.dumps({"query": "矩阵"})}}]}
    monkeypatch.setattr(mcp_tools, "connect", connect)
    monkeypatch.setattr(mcp_tools, "discover", discover)
    monkeypatch.setattr(mcp_tools, "invoke", invoke)
    monkeypatch.setattr(workflow, "publish", lambda *args, **kwargs: None)
    budget = {}
    state = {"task": {"config_snapshot": {"mcp_servers": {"mock": {"name": "mock", "timeout_seconds": 30}},
        "input_token_budget": 32768, "limits": dict(platform.DEFAULT_LIMITS)}}, "gateway": ToolGateway(),
        "sources": [], "degradation": [], "context": [], "search_needed": False, "search_query": "矩阵",
        "intent": {"retrieval_mode": "auto"}, "shared_budget": budget}
    await workflow.web_search(state)
    assert budget["tool_rounds_used"] == 3
    assert budget["tool_slots_used"] == 3
    assert len(invocations) == 1
    later = await workflow.web_search({**state, "gateway": ToolGateway()})
    assert len(invocations) == 1 and len(connections) == 1
    assert "预算已用尽" in "".join(later["degradation"])


async def test_generated_explanation_is_saved_once_and_current_answer_read_is_model_free(client, resource, monkeypatch):
    monkeypatch.setattr(workflow, "Gateway", FakeGateway)
    agent, conversation = resource["agent"]["id"], resource["conversation"]["id"]
    created = post(client, f"/agents/{agent}/exercises", {"question_text": "矩阵乘法条件？", "source_ids": [], "links": []})
    assert created.status_code == 201, created.text
    exercise = created.json()["data"]
    accepted = post(client, f"/exercises/{exercise['id']}/explanation-tasks", {
        "conversation_id": conversation, "expected_version": exercise["content_version"]})
    assert accepted.status_code == 202, accepted.text
    await run_task(claim("derive-answer"))
    task = client.get("/api/v1/tasks/" + accepted.json()["data"]["task"]["id"]).json()["data"]
    assert task["status"] == "succeeded", task
    answer = client.get(f"/api/v1/exercises/{exercise['id']}/answer").json()["data"]
    assert answer["available"] and answer["version"] == 2
    assert answer["origin_kind"] == "derived" and "矩阵乘法" in answer["answer_text"]
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.tasks)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.exercise_revisions)) == 2


async def test_history_card_and_legacy_answer_request_keep_original_revision(client, resource, gateway):
    result = await request(client, resource)
    exercise_id = result["result"]["exercise_ids"][0]
    with db.engine.begin() as conn:
        original = db.one(conn, db.exercises, UUID(exercise_id))
        changed = db.update(conn, db.exercises, original["id"], question_text="用户后来改写的题目", content_version=2)
        from study_trail.learning_content import record_exercise
        record_exercise(conn, changed, "user_edited")
        message = c.message(conn, db.one(conn, db.messages, UUID(result["assistant_message_id"])))
        assert message["exercises"][0]["question_text"] == "矩阵有几行几列？"
    legacy = post(client, f"/exercises/{exercise_id}/answer", {})
    assert legacy.status_code == 202, legacy.text
    with db.engine.begin() as conn:
        frozen = db.one(conn, db.tasks, UUID(legacy.json()["data"]["task"]["id"]))
        assert frozen["input_snapshot"]["exercise_snapshot"]["question_text"] == "矩阵有几行几列？"
        assert frozen["input_snapshot"]["exercise_snapshot"]["content_version"] == 1
        conn.execute(db.exercises.delete().where(db.exercises.c.id == original["id"]))
        after_delete = c.message(conn, db.one(conn, db.messages, UUID(result["assistant_message_id"])))
        assert after_delete["exercises"][0]["question_text"] == "矩阵有几行几列？"


async def test_explanation_revision_conflict_rolls_back_formal_reply(client, resource, monkeypatch):
    agent, conversation = resource["agent"]["id"], resource["conversation"]["id"]
    exercise = post(client, f"/agents/{agent}/exercises", {"question_text": "旧题", "source_ids": [], "links": []}).json()["data"]
    response = post(client, f"/exercises/{exercise['id']}/explanation-tasks", {
        "conversation_id": conversation, "expected_version": 1})
    with db.engine.begin() as conn:
        db.update(conn, db.exercises, UUID(exercise["id"]), content_version=2, question_text="新版题")
    monkeypatch.setattr(workflow, "Gateway", FakeGateway)
    await run_task(claim("derive-conflict"))
    task = client.get("/api/v1/tasks/" + response.json()["data"]["task"]["id"]).json()["data"]
    assert task["status"] == "failed" and task["error"]["code"] == "VERSION_CONFLICT", task
    with db.engine.connect() as conn:
        current = db.one(conn, db.exercises, UUID(exercise["id"]))
        assert current["question_text"] == "新版题" and current["known_answer_snapshot"] is None


async def test_composite_plan_reply_uses_commit_time_name_after_concurrent_rename(client, resource, monkeypatch):
    from .test_workflow import payload
    from study_trail.learning_content import record_plan
    original = payload()
    original.pop("title")
    with db.engine.begin() as conn:
        plan = db.insert(conn, db.learning_plans, owner_id=UUID(resource["student"]["id"]),
                         agent_id=UUID(resource["agent"]["id"]), name="运行开始时的旧名称", content=original)
        record_plan(conn, plan)
    class RenameDuringSummary(CollaborationGateway):
        async def structured(self, prompt, messages=None):
            result = await super().structured(prompt, messages)
            if "判定当前学生请求意图" in prompt:
                result["action"] = "plan_update"
            return result
        async def complete(self, messages, json_mode=False, on_chunk=None):
            if not self.task.get("role_run_id"):
                with db.engine.begin() as conn:
                    db.update(conn, db.learning_plans, plan["id"], name="生成期间重新命名")
            return await super().complete(messages, json_mode, on_chunk)
    RenameDuringSummary.fail_role, RenameDuringSummary.fail_summary = None, False
    monkeypatch.setattr(workflow, "Gateway", RenameDuringSummary)
    accepted = post(client, f"/conversations/{resource['conversation']['id']}/messages", {
        "content_text": "请修改这份计划，讲解矩阵，并根据计划出题", "target_plan_id": str(plan["id"]),
        "expected_plan_version": 1})
    assert accepted.status_code == 202, accepted.text
    await run_task(claim("rename-composite"))
    result = client.get("/api/v1/tasks/" + accepted.json()["data"]["task"]["id"]).json()["data"]
    assert result["status"] == "succeeded", result
    reply = result["result"]["preview_text"]
    assert "# 生成期间重新命名" in reply and "# 运行开始时的旧名称" not in reply
    assert "矩阵乘法" in reply and "矩阵有几行几列？" in reply
    with db.engine.connect() as conn:
        saved = db.one(conn, db.learning_plans, plan["id"])
        assert saved["name"] == "生成期间重新命名" and saved["content_version"] == 2
        message = db.one(conn, db.messages, UUID(result["assistant_message_id"]))
        assert message["content_text"] == reply


async def test_composite_retry_keeps_frozen_exercise_after_new_question_in_conversation(client, resource, monkeypatch):
    class RetryGateway(FakeGateway):
        fail_summary = False
        observed = []
        async def structured(self, prompt, messages=None):
            text = self.task["input_snapshot"]["content_text"]
            if "判定当前学生请求意图" in prompt:
                composite = "解析刚才" in text
                return {"action": "plan_create" if composite else "exercise", "target_hint": None,
                    "deadline": None, "time_limit_minutes": None, "retrieval_mode": "none",
                    "dispatch": [{"step_id": "plan", "role": "plan", "purpose": "制定计划", "depends_on": [], "target_ids": []},
                                 {"step_id": "explanation", "role": "explanation", "purpose": "解析刚才第一题", "depends_on": [], "target_ids": []}]
                                if composite else [{"step_id": "exercise", "role": "exercise", "purpose": "出题", "depends_on": [], "target_ids": []}]}
            if "为学生生成练习题" in prompt:
                return {"exercises": [{"question_text": "题B" if "第二轮" in text else "题A",
                    "provenance": "generated", "source_ids": [], "known_answer_snapshot": None}]}
            return await super().structured(prompt, messages)
        async def complete(self, messages, json_mode=False, on_chunk=None):
            frozen = self.task["input_snapshot"].get("exercise_snapshot")
            if self.task.get("role_run_id") and frozen:
                self.observed.append(frozen["question_text"])
            if not self.task.get("role_run_id") and self.fail_summary and "解析刚才" in self.task["input_snapshot"]["content_text"]:
                raise c.Problem("MODEL_UNAVAILABLE", "合成汇总失败", 503)
            return await super().complete(messages, json_mode, on_chunk)
    monkeypatch.setattr(workflow, "Gateway", RetryGateway)
    path = f"/conversations/{resource['conversation']['id']}/messages"
    first = post(client, path, {"content_text": "第一轮出题"}).json()["data"]
    await run_task(claim("question-a"))
    first_task = client.get("/api/v1/tasks/" + first["task"]["id"]).json()["data"]
    assert first_task["status"] == "succeeded", first_task
    question_a = first_task["result"]["exercise_ids"][0]
    RetryGateway.fail_summary = True
    original = post(client, path, {"content_text": "请制定计划，并解析刚才第1题"}).json()["data"]
    await run_task(claim("composite-original"))
    failed = client.get("/api/v1/tasks/" + original["task"]["id"]).json()["data"]
    assert failed["status"] == "failed", failed
    RetryGateway.fail_summary = False
    second = post(client, path, {"content_text": "第二轮出题"}).json()["data"]
    await run_task(claim("question-b"))
    second_task = client.get("/api/v1/tasks/" + second["task"]["id"]).json()["data"]
    assert second_task["status"] == "succeeded", second_task
    assert second_task["result"]["exercise_ids"][0] != question_a
    retried = post(client, f"/messages/{original['user_message']['id']}/retry", {
        "previous_task_id": failed["id"]})
    assert retried.status_code == 202, retried.text
    with db.engine.connect() as conn:
        accepted = db.one(conn, db.tasks, UUID(retried.json()["data"]["task"]["id"]))
        assert accepted["input_snapshot"]["dispatch_exercise_id"] == question_a
        assert accepted["input_snapshot"]["exercise_snapshot"]["question_text"] == "题A"
    RetryGateway.observed = []
    await run_task(claim("composite-retry"))
    result = client.get("/api/v1/tasks/" + retried.json()["data"]["task"]["id"]).json()["data"]
    assert result["status"] == "succeeded", result
    assert RetryGateway.observed == ["题A"]
