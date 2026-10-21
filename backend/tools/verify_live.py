"""Bounded real-provider verification on a dedicated ordinary account.

Run with the normal worker stopped so only explicitly selected verification tasks
are claimed. Actual invocation attempts share a hard limit of 30 for this run.
No upstream response body or credential is written to the public report.
"""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from fastapi.testclient import TestClient

from study_trail import db
from study_trail.api import app
from study_trail.execution import claim, run_task
from study_trail.telemetry import verification_budget


async def main():
    if db.engine.url.database != "study_trail":
        raise RuntimeError("This verifier expects the upgraded local installation.")
    root = Path(__file__).parents[2]
    output = root / "docs/verification-live-v0.0.2.json"
    if output.exists():
        raise RuntimeError(
            "A live verification report already exists; do not repeat paid calls automatically."
        )
    with db.engine.connect() as conn:
        if conn.exec_driver_sql("SELECT count(*) FROM tasks WHERE status IN ('queued','running')").scalar():
            raise RuntimeError("Wait for existing tasks before dedicated verification.")
    budget = {"used": 0, "limit": 30}
    token = verification_budget.set(budget)
    results = []
    tasks = []
    task_ids = []
    owner = None

    def record(name, status, code=None):
        result = {"scenario": name, "status": status, "safe_error_code": code}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.headers["Origin"] = "http://127.0.0.1:5173"
            login = "verify_" + uuid4().hex[:16]
            password = uuid4().hex
            registered = client.post(
                "/api/v1/auth/register",
                json={"login": login, "password": password, "confirm_password": password},
            )
            if registered.status_code != 201:
                raise RuntimeError("Verification account registration failed.")
            owner = UUID(registered.json()["data"]["id"])
            signed = client.post("/api/v1/auth/login", json={"login": login, "password": password}).json()[
                "data"
            ]
            client.headers["X-CSRF-Token"] = signed["csrf_token"]

            def post(path, body):
                return client.post("/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid4())})

            async def execute(name, response, file=False):
                if response.status_code != 202:
                    code = response.json().get("error", {}).get("code", "REQUEST_REJECTED")
                    record(name, "failed", code)
                    return None
                data = response.json()["data"]
                task = data["items"][0]["task"] if file else data["task"]
                task_id = UUID(task["id"])
                task_ids.append(task_id)
                claimed = claim("live-verification", task_id)
                if not claimed:
                    raise RuntimeError("Verification task was unexpectedly claimed by another worker.")
                await run_task(claimed)
                with db.engine.connect() as conn:
                    row = db.one(conn, db.tasks, task_id)
                    record(name, row["status"], row["error_code"])
                    tasks.append(
                        {
                            "scenario": name,
                            "task_id": str(task_id),
                            "status": row["status"],
                            "safe_error_code": row["error_code"],
                        }
                    )
                    return dict(row)

            agent_response = client.post(
                "/api/v1/agents",
                json={
                    "name": "版本验收学习伙伴",
                    "system_prompt": "用中文简洁讲解，引用实际资料，明确模型建议。",
                    "mcp_server_ids": [],
                    "skill_ids": ["explain", "practice", "study-plan"],
                },
            )
            if agent_response.status_code != 201:
                raise RuntimeError("Verification agent creation failed.")
            agent = agent_response.json()["data"]
            conversation = client.post(f"/api/v1/agents/{agent['id']}/conversations", json={}).json()["data"]

            def chat(text, skill=None):
                return post(
                    f"/conversations/{conversation['id']}/messages", {"content_text": text, "skill_id": skill}
                )

            basic = await execute("真实生成与流式保存", chat("用两句话解释矩阵乘法的维度要求。", "explain"))
            # A failing provider is not retried automatically or repeatedly tested by another scenario.
            if basic and basic["status"] == "succeeded":
                kb = client.post("/api/v1/knowledge-bases", json={"name": "验收专用资料"}).json()["data"]
                uploaded = client.post(
                    f"/api/v1/knowledge-bases/{kb['id']}/files",
                    files=[
                        (
                            "files",
                            (
                                "verification.txt",
                                "矩阵乘法要求：左侧矩阵的列数等于右侧矩阵的行数。若A为2行3列，B为3行4列，AB为2行4列。".encode(),
                                "text/plain",
                            ),
                        )
                    ],
                    headers={"Idempotency-Key": str(uuid4())},
                )
                file_task = await execute("真实文件嵌入发布", uploaded, True)
                if file_task and file_task["status"] == "succeeded":
                    client.patch("/api/v1/agents/" + agent["id"], json={"knowledge_base_ids": [kb["id"]]})
                    await execute(
                        "真实RAG嵌入查询与重排",
                        chat("依据所上传的资料，A是2行3列、B是3行4列，AB的维度是什么？"),
                    )
                await execute(
                    "真实结构化学习计划保存",
                    chat(
                        "请制定一个向量入门学习计划，两个阶段，每阶段一个具体任务，不设截止日期。",
                        "study-plan",
                    ),
                )
                await execute(
                    "真实提示词辅助",
                    post(
                        "/prompt-tasks",
                        {"mode": "generate", "name": "数学入门", "description": "简洁清晰地辅导数学"},
                    ),
                )
            else:
                record("RAG及计划等后续生成场景", "not_verified", "GENERATION_PREREQUISITE_FAILED")
            # MCP is independent of the generation prerequisite; discover then perform one actual allowed call.
            from study_trail import platform, mcp_tools

            with db.engine.begin() as conn:
                frozen = platform.snapshot(conn)
            available = platform.catalog("mcp")
            if "exa" in available:
                server = dict(available["exa"])
                server["_task"] = {"owner_id": owner, "kind": "chat", "config_snapshot": frozen}
                try:
                    async with asyncio.timeout(30), mcp_tools.connect(server, 30) as session:
                        tools = await mcp_tools.discover(session, "exa", server, 30)
                        tool = next((t for t in tools if t["name"] == "web_search_exa"), None)
                        if not tool:
                            raise RuntimeError("MCP_TOOL_NOT_DISCOVERED")
                        arguments = {"query": "矩阵乘法 维度 教育", "numResults": 2}
                        if "objective" in tool["input_schema"].get("required", []):
                            arguments["objective"] = "核对教育资料中的矩阵乘法维度要求"
                        await mcp_tools.invoke(session, server, tool, arguments, 30)
                    record("真实MCP工具发现与调用", "succeeded")
                except Exception as exc:
                    record("真实MCP工具发现与调用", "failed", getattr(exc, "code", "MCP_UNAVAILABLE"))
            else:
                record("真实MCP工具发现与调用", "not_verified", "MCP_NOT_ENABLED")
    finally:
        verification_budget.reset(token)
        with db.engine.connect() as conn:
            counts = (
                {
                    str(kind): count
                    for kind, count in conn.execute(
                        sa.select(db.invocation_attempts.c.model_kind, sa.func.count())
                        .where(db.invocation_attempts.c.actor_id == owner)
                        .group_by(db.invocation_attempts.c.model_kind)
                    ).all()
                }
                if owner
                else {}
            )
        output.write_text(
            json.dumps(
                {
                    "physical_attempts": budget["used"],
                    "maximum": 30,
                    "results": results,
                    "tasks": tasks,
                    "attempts_by_model_kind": counts,
                    "finished_at": db.now().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Real verification attempts:", budget["used"], "/ 30", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
