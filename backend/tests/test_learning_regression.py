"""Current v0.0.1 user workflows with deterministic provider output."""
from uuid import UUID

from study_trail import db
from study_trail.execution import claim, run_task
from .test_api import post
from .test_workflow import FakeGateway, payload


class LearningGateway(FakeGateway):
    async def structured(self, prompt, messages=None):
        text = self.task["input_snapshot"]["content_text"]
        if "判定当前学生请求意图" in prompt:
            action = "plan_update" if "修改" in text else "feedback" if "我的答案" in text else "exercise" if "出题" in text else "plan_create"
            return {"action": action, "target_hint": None, "deadline": None, "time_limit_minutes": None}
        if "covered" in prompt:
            return {"covered": True, "missing_query": ""}
        if '"exercises"' in prompt:
            return {"exercises": [{"question_text": "一加一等于几？", "provenance": "generated", "source_ids": [], "known_answer_snapshot": None}]}
        plan = payload()
        if "修改" in text:
            plan["stages"][0]["tasks"][0]["estimated_minutes"] = 45
        return plan


async def execute(client, response):
    assert response.status_code == 202, response.text
    task_id = UUID(response.json()["data"]["task"]["id"])
    await run_task(claim("learning-regression", task_id))
    result = client.get("/api/v1/tasks/" + str(task_id)).json()["data"]
    assert result["status"] == "succeeded", result
    return result


async def test_cross_conversation_plan_update_keeps_other_plan(client, resource, monkeypatch):
    monkeypatch.setattr("study_trail.workflow.Gateway", LearningGateway)
    client.patch("/api/v1/agents/" + resource["agent"]["id"], json={"mcp_server_ids": []})
    path = f"/conversations/{resource['conversation']['id']}/messages"
    first = await execute(client, post(client, path, {"content_text": "制定学习计划"}))
    second = await execute(client, post(client, path, {"content_text": "再制定学习计划"}))
    first_id, second_id = first["result"]["plan_mutation"]["plan_id"], second["result"]["plan_mutation"]["plan_id"]
    second_before = client.get("/api/v1/plans/" + second_id).json()["data"]
    other = client.post(f"/api/v1/agents/{resource['agent']['id']}/conversations", json={}).json()["data"]
    updated = await execute(client, post(client, f"/conversations/{other['id']}/messages", {"content_text": "修改这份计划，每项任务调整到45分钟", "target_plan_id": first_id, "expected_plan_version": 1}))
    assert updated["result"]["plan_mutation"]["plan_id"] == first_id
    first_after = client.get("/api/v1/plans/" + first_id).json()["data"]
    assert first_after["content_version"] == 2
    assert first_after["content"]["stages"][0]["tasks"][0]["estimated_minutes"] == 45
    assert client.get("/api/v1/plans/" + second_id).json()["data"] == second_before


async def test_exercise_answer_and_single_question_feedback(client, resource, monkeypatch):
    monkeypatch.setattr("study_trail.workflow.Gateway", LearningGateway)
    client.patch("/api/v1/agents/" + resource["agent"]["id"], json={"mcp_server_ids": []})
    path = f"/conversations/{resource['conversation']['id']}/messages"
    exercise_task = await execute(client, post(client, path, {"content_text": "请出题"}))
    ident = exercise_task["result"]["exercise_ids"][0]
    exercise = client.get("/api/v1/exercises/" + ident).json()["data"]
    assert exercise["provenance"] == "generated"
    assert "答案" not in exercise_task["result"]["preview_text"]
    answer = await execute(client, post(client, f"/exercises/{ident}/answer", {}))
    assert answer["result"]["preview_text"]
    feedback = await execute(client, post(client, path, {"content_text": "第一题我的答案是2，请反馈"}))
    with db.engine.connect() as conn:
        task = db.one(conn, db.tasks, UUID(feedback["id"]))
        assert task["action"] == "feedback"
        assert task["input_snapshot"]["feedback_exercise_id"] == ident
    assert not feedback["result"].get("plan_mutation")
