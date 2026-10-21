from uuid import uuid4

import pytest

from study_trail import core as c
from study_trail.workflow import answer_stream, render_plan, validate_answer_sources


def test_plan_retains_task_source_ids():
    first, second = str(uuid4()), str(uuid4())
    value = {
        "title": "学习计划", "goal": "掌握时态", "deadline": None,
        "stages": [{"title": "入门", "goal": "理解", "knowledge_points": ["时态"],
                    "tasks": [{"title": "阅读", "estimated_minutes": 10,
                               "resource_source_ids": [first, first, second], "exercise_suggestions": []}]}],
    }
    answer = render_plan(value)
    assert answer.count(f"source:{first}") == 1
    assert answer.count(f"source:{second}") == 1
    validate_answer_sources(answer, [{"id": first}, {"id": second}])
    with pytest.raises(c.Problem):
        validate_answer_sources(answer, [{"id": first}])
    validate_answer_sources(f"[来源](source:{first.upper()})", [{"id": first}])
    with pytest.raises(c.Problem):
        validate_answer_sources("[错误](source:------------------------------------)", [])


@pytest.mark.asyncio
async def test_exercise_citations_follow_the_question_without_revealing_answer(monkeypatch):
    ident = str(uuid4())

    class Gateway:
        async def structured(self, *args, **kwargs):
            return {"exercises": [{"question_text": "选择正确时态", "provenance": "adapted",
                                   "source_ids": [ident], "known_answer_snapshot": None}]}

    monkeypatch.setattr("study_trail.workflow.publish", lambda *args, **kwargs: None)
    result = await answer_stream({
        "task": {"config_snapshot": {"input_token_budget": 32768}}, "action": "exercise",
        "gateway": Gateway(), "context": [], "degradation": [],
        "sources": [{"id": ident, "excerpt": "选择正确时态"}],
    })
    assert f"选择正确时态 [来源](source:{ident})" in result["answer"]
    assert "答案" not in result["answer"]
