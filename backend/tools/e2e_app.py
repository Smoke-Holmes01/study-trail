"""Deterministic browser-test providers; refuses to run against any non-E2E database."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa

from study_trail.config import settings

if sa.engine.make_url(settings().database_url).database != "study_trail_e2e":
    raise RuntimeError("Fake providers may only run against study_trail_e2e.")


class FakeGateway:
    def __init__(self, task):
        self.task = task

    def timeout(self, maximum):
        return maximum

    async def structured(self, prompt, messages=None):
        await asyncio.sleep(0.15)
        text = self.task.get("input_snapshot", {}).get("content_text", "")
        if "判定当前学生请求意图" in prompt:
            action = "plan_create" if "计划" in text and any(x in text for x in ("制定", "创建")) else "tutor"
            return {"action": action, "target_hint": None, "deadline": None, "time_limit_minutes": None}
        if "covered" in prompt:
            return {"covered": True, "missing_query": ""}
        if '"exercises"' in prompt and "PlanPayload" not in prompt:
            return {
                "exercises": [
                    {
                        "question_text": "计算两个二维向量的和。",
                        "provenance": "generated",
                        "source_ids": [],
                        "known_answer_snapshot": None,
                    }
                ]
            }
        return {
            "title": "向量基础学习计划",
            "goal": "掌握向量与矩阵基础",
            "deadline": None,
            "stages": [
                {
                    "id": str(uuid4()),
                    "title": "基础阶段",
                    "goal": "理解向量",
                    "knowledge_points": ["向量维度"],
                    "tasks": [
                        {
                            "id": str(uuid4()),
                            "title": "练习向量计算",
                            "estimated_minutes": 30,
                            "resource_source_ids": [],
                            "exercise_suggestions": ["计算两个向量的和"],
                        }
                    ],
                },
                {
                    "id": str(uuid4()),
                    "title": "应用阶段",
                    "goal": "应用向量",
                    "knowledge_points": ["矩阵维度"],
                    "tasks": [
                        {
                            "id": str(uuid4()),
                            "title": "判断矩阵乘法维度",
                            "estimated_minutes": 30,
                            "resource_source_ids": [],
                            "exercise_suggestions": ["判断矩阵是否可相乘"],
                        }
                    ],
                },
            ],
        }

    async def complete(self, messages, json_mode=False, on_chunk=None):
        text = "矩阵乘法要求左矩阵列数等于右矩阵行数。单位向量的长度为1。此处为模拟提供商的学习建议。"
        if on_chunk:
            for end in (10, 25, len(text)):
                await asyncio.sleep(0.4)
                await on_chunk(text[:end])
        return text

    async def embed(self, texts):
        await asyncio.sleep(0.5)
        return [[1.0] + [0.0] * 1023 for _ in texts]

    async def rerank(self, query, docs):
        return list(range(min(6, len(docs))))

    async def tool_turn(self, messages, tools):
        return {"role": "assistant", "content": None, "tool_calls": []}


@asynccontextmanager
async def connection(*args, **kwargs):
    yield object()


async def discover(*args, **kwargs):
    return []


from study_trail import mcp_tools, providers, workflow

providers.Gateway = workflow.Gateway = FakeGateway
mcp_tools.connect = connection
mcp_tools.discover = discover
from study_trail.api import app as app

if __name__ == "__main__":
    from study_trail.execution import worker

    logging.basicConfig(level=logging.INFO)
    asyncio.run(worker())
