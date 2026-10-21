import json
from uuid import UUID
import sqlalchemy as sa
from study_trail import db, core as c
from study_trail.workflow import context
from study_trail.execution import claim
from .test_api import post


async def test_summary_keeps_recent_turns_and_unpaired_student_conditions(client, resource):
    owner = UUID(resource["student"]["id"])
    agent = UUID(resource["agent"]["id"])
    conv = UUID(resource["conversation"]["id"])
    with db.engine.begin() as conn:
        seq = 1
        for i in range(14):
            text = ("旧知识点" * 10) if i < 8 else f"最近轮次{i}"
            user = db.insert(
                conn,
                db.messages,
                owner_id=owner,
                agent_id=agent,
                conversation_id=conv,
                sequence_no=seq,
                role="user",
                origin="user_input",
                content_text=text,
            )
            db.insert(
                conn,
                db.messages,
                owner_id=owner,
                agent_id=agent,
                conversation_id=conv,
                sequence_no=seq + 1,
                role="assistant",
                origin="assistant",
                content_text=("已掌握" * 10) if i < 8 else f"最近回答{i}",
                user_message_id=user["id"],
                response_status="succeeded",
            )
            seq += 2
        unmatched = db.insert(
            conn,
            db.messages,
            owner_id=owner,
            agent_id=agent,
            conversation_id=conv,
            sequence_no=seq,
            role="user",
            origin="user_input",
            content_text="尚未配对的学生条件：每周三天。",
        )
        db.update(conn, db.conversations, conv, next_sequence_no=seq + 1)
    post(client, f"/conversations/{conv}/messages", {"content_text": "继续解释"})
    task = claim("test")
    task["config_snapshot"]["input_token_budget"] = 1200

    class SummaryGateway:
        async def complete(self, *args, **kwargs):
            return "旧轮次摘要：已讲解基础知识，保留学习条件。"

    result = await context({"task": task, "gateway": SummaryGateway(), "target": None, "sources": []})
    text = json.dumps(result["context"], ensure_ascii=False)
    assert "尚未配对的学生条件" in text
    assert "最近轮次13" in text and "最近轮次8" in text
    with db.engine.begin() as conn:
        summary = conn.execute(sa.select(db.conversation_summaries)).mappings().one()
        assert len(summary["covered_user_message_ids"]) == 8
        assert unmatched["id"] not in summary["covered_user_message_ids"]


def test_preview_does_not_duplicate_rag_overlap(client, resource):
    from study_trail.files import chunks

    owner = UUID(resource["student"]["id"])
    kb = client.post("/api/v1/knowledge-bases", json={"name": "长文本"}).json()["data"]
    text = "\n".join(f"第{i}段：学习矩阵维度与乘法规则。" for i in range(200)) + "\n"
    metadata = {"boundaries": [{"start": 0, "end": len(text), "line": 1}]}
    with db.engine.begin() as conn:
        file = db.insert(
            conn,
            db.files,
            owner_id=owner,
            knowledge_base_id=UUID(kb["id"]),
            original_name="长文.txt",
            extension=".txt",
            media_type="text/plain",
            storage_key=c.store_bytes(text.encode()),
            sha256=b"a" * 32,
            byte_size=len(text.encode()),
            status="ready",
            text_snapshot=text,
            text_metadata=metadata,
        )
        for chunk in chunks(text, metadata):
            db.insert(
                conn,
                db.document_chunks,
                owner_id=owner,
                knowledge_base_id=UUID(kb["id"]),
                file_id=file["id"],
                parse_version=1,
                embedding=[1.0] + [0.0] * 1023,
                **chunk,
            )
    cursor = None
    output = ""
    while True:
        path = f"/api/v1/files/{file['id']}/preview?limit=1" + ("&cursor=" + cursor if cursor else "")
        result = client.get(path).json()["data"]
        output += "".join(b["text"] for b in result["blocks"])
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert output == text
