"""Business-level v0.1.0 content, template and import regression."""
from uuid import UUID, uuid4

import sqlalchemy as sa

from study_trail import db
from study_trail.execution import claim, run_task

from .test_management import admin as admin  # noqa: PLC0414 -- expose the shared pytest fixture
from .test_workflow import payload


def write(client, method, path, value):
    response = client.request(method, path, json=value, headers={"Idempotency-Key": str(uuid4())})
    return response

def test_independent_exercise_revision_hidden_answer_and_agent_boundary(client, resource):
    base = "/api/v1/agents/" + resource["agent"]["id"] + "/exercises"
    created = write(client, "POST", base, {"question_text": "1+1=?", "answer_text": "2", "explanation": "加法", "links": [], "source_ids": []})
    assert created.status_code == 201, created.text
    item = created.json()["data"]
    assert "answer_text" not in item
    assert item["conversation_id"] is None
    initial = client.get(f"/api/v1/exercises/{item['id']}/answer?version=1").json()["data"]
    assert initial["answer_text"] == "2"
    updated = write(client, "PATCH", f"/api/v1/exercises/{item['id']}", {"expected_version": 1, "question_text": "2+2=?", "answer_text": "4", "source_ids": [], "links": []})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["content_version"] == 2
    assert client.get(f"/api/v1/exercises/{item['id']}/answer?version=1").json()["data"] == initial
    conflict = write(client, "PATCH", f"/api/v1/exercises/{item['id']}", {"expected_version": 1, "question_text": "旧草稿", "source_ids": [], "links": []})
    assert conflict.status_code == 409
    # Removing a source conversation does not remove independent exercises.
    assert client.delete(f"/api/v1/conversations/{resource['conversation']['id']}").status_code == 200
    assert client.get(base).json()["data"]["items"][0]["id"] == item["id"]

def test_answer_keeps_its_own_source_after_source_conversation_is_deleted(client, resource):
    from study_trail import core
    from study_trail.learning_content import record_exercise
    owner, agent, conversation = (UUID(resource[k]["id"]) for k in ("student", "agent", "conversation"))
    with db.engine.begin() as conn:
        question_source = db.insert(conn, db.sources, owner_id=owner, kind="web", title_snapshot="题目出处", url_snapshot="https://example.invalid/question", excerpt_snapshot="题干")
        answer_source = db.insert(conn, db.sources, owner_id=owner, kind="web", title_snapshot="答案出处", url_snapshot="https://example.invalid/answer", excerpt_snapshot="答案原文")
        message = db.insert(conn, db.messages, owner_id=owner, agent_id=agent, conversation_id=conversation,
            sequence_no=1, role="assistant", origin="assistant", content_text="题目", response_status="succeeded")
        exercise = db.insert(conn, db.exercises, owner_id=owner, agent_id=agent, conversation_id=conversation,
            message_id=message["id"], position=0, question_text="题干", provenance="original", source_ids=[str(question_source["id"])],
            known_answer_snapshot={"answer_text": "答案原文", "source_ids": [str(answer_source["id"])]})
        exercise = record_exercise(conn, exercise)
        _, frozen = core.freeze_exercise(conn, exercise)
        assert frozen["known_answer_snapshot"]["source_ids"] == [str(answer_source["id"])]
    initial = client.get(f"/api/v1/exercises/{exercise['id']}").json()["data"]
    assert initial["source_ids"] == [str(question_source["id"])] and "answer_text" not in initial
    assert client.delete(f"/api/v1/conversations/{conversation}").status_code == 200
    assert client.get(f"/api/v1/sources/{answer_source['id']}").status_code == 200
    answer = client.get(f"/api/v1/exercises/{exercise['id']}/answer").json()["data"]
    assert answer["source_ids"] == [str(answer_source["id"])]
    assert answer["sources"][0]["title"] == "答案出处" and answer["origin_kind"] == "original"
    updated = write(client, "PATCH", f"/api/v1/exercises/{exercise['id']}", {
        "expected_version": 1, "question_text": "用户编辑后的题干", "answer_text": "答案原文",
        "source_ids": [str(question_source["id"])], "answer_source_ids": [str(answer_source["id"])], "links": []})
    assert updated.status_code == 200, updated.text
    assert client.get(f"/api/v1/exercises/{exercise['id']}/answer").json()["data"]["source_ids"] == [str(answer_source["id"])]
    assert client.get(f"/api/v1/exercises/{exercise['id']}/answer?version=1").json()["data"] == answer


def test_direct_plan_edit_preserves_name_ids_and_revision(client, resource):
    plan_payload = payload()
    with db.engine.begin() as conn:
        plan = db.insert(conn, db.learning_plans, owner_id=UUID(resource["student"]["id"]), agent_id=UUID(resource["agent"]["id"]), name="原计划名称", content={k: v for k, v in plan_payload.items() if k != "title"})
    response = write(client, "PUT", f"/api/v1/plans/{plan['id']}/content", {"expected_content_version": 1, "content": plan_payload})
    assert response.status_code == 200, response.text
    out = response.json()["data"]
    assert out["name"] == "原计划名称"
    assert out["content_version"] == 2
    assert out["content"]["stages"][0]["id"] == plan_payload["stages"][0]["id"]
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.learning_plan_revisions).where(db.learning_plan_revisions.c.plan_id == plan["id"])) == 1
    assert write(client, "PUT", f"/api/v1/plans/{plan['id']}/content", {"expected_content_version": 1, "content": plan_payload}).status_code == 409

async def published_template(admin):
    created = write(admin, "POST", "/api/admin/agent-templates", {"name": "向量模板", "description": "基础练习"})
    assert created.status_code == 201, created.text
    template = created.json()["data"]
    version = template["versions"][0]
    upload = admin.post(f"/api/admin/agent-templates/{template['id']}/assets", files=[("files", ("教材.txt", "向量可以相加。单位向量长度为一。".encode(), "text/plain"))], headers={"Idempotency-Key": str(uuid4())})
    assert upload.status_code == 202, upload.text
    entry = upload.json()["data"]["items"][0]
    task = claim("asset-test", UUID(entry["task"]["id"]))
    await run_task(task)
    asset = admin.get(f"/api/admin/agent-templates/{template['id']}/assets").json()["data"]["items"][0]
    assert asset["status"] == "ready"
    models = admin.get("/api/admin/configs/model").json()["data"]["items"]
    model = next(x["stable_id"] for x in models if x["payload"]["model_kind"] == "generative")
    content = payload()
    content["stages"][0]["tasks"][0]["resource_source_ids"] = [asset["id"]]
    body = {"config_choices": {"system_prompt": "解释向量", "model_id": model, "mcp_server_ids": [], "skill_ids": []}, "plan": content,
        "exercises": [{"question_text": "单位向量长度是多少？", "answer_text": "1", "explanation": "定义", "source_ids": [asset["id"]], "provenance": "original", "links": []}], "asset_ids": [asset["id"]]}
    saved = write(admin, "PUT", f"/api/admin/template-versions/{version['id']}", {"expected_version": version["lock_version"], "payload": body})
    assert saved.status_code == 200, saved.text
    version = saved.json()["data"]
    published = write(admin, "POST", f"/api/admin/template-versions/{version['id']}/publish", {"expected_version": version["lock_version"]})
    assert published.status_code == 200, published.text
    return published.json()["data"], version, asset

async def test_template_publish_adopt_independent_and_answers_hidden(admin, client, student):
    template, version, _asset = await published_template(admin)
    preview = client.get(f"/api/v1/agent-templates/{template['id']}/versions/{version['id']}")
    assert preview.status_code == 200, preview.text
    assert "answer_text" not in preview.json()["data"]["exercises"][0]
    assert "parsed_content" not in preview.text
    assert client.get(f"/api/v1/agent-templates/{template['id']}/versions/{version['id']}?include_answers=true").json()["data"]["exercises"][0]["answer_text"] == "1"
    key = str(uuid4())
    adopted = client.post("/api/v1/template-adoptions", json={"version_id": version["id"]}, headers={"Idempotency-Key": key})
    assert adopted.status_code == 201, adopted.text
    own = adopted.json()["data"]
    assert client.post("/api/v1/template-adoptions", json={"version_id": version["id"]}, headers={"Idempotency-Key": key}).json()["data"] == own
    sources = client.get(f"/api/v1/plans/{own['plan_ids'][0]}").json()["data"]["sources"]
    assert sources[0]["status"] == "template_not_imported"
    assert sources[0]["excerpt"] == ""
    denied = client.get(f"/api/v1/sources/{sources[0]['id']}/preview")
    assert denied.status_code == 409
    assert write(admin, "PUT", f"/api/admin/template-versions/{version['id']}", {"expected_version": version["lock_version"] + 1, "payload": {}}).status_code == 409
    offline = write(admin, "POST", f"/api/admin/agent-templates/{template['id']}/offline", {"expected_version": template["lock_version"]})
    assert offline.status_code == 200
    assert client.get(f"/api/v1/template-adoptions/{own['id']}").status_code == 200
    assert client.get(f"/api/v1/plans/{own['plan_ids'][0]}").status_code == 200
    assert client.post("/api/v1/template-adoptions", json={"version_id": version["id"]}, headers={"Idempotency-Key": str(uuid4())}).status_code == 409

async def test_import_failure_keeps_adopted_instance_and_retries_without_duplicates(admin, client, student, monkeypatch):
    _template, version, _ = await published_template(admin)
    own = write(client, "POST", "/api/v1/template-adoptions", {"version_id": version["id"], "import_target": {"new_kb_name": "模板个人资料"}}).json()["data"]
    with db.engine.connect() as conn:
        import_task = conn.execute(sa.select(db.tasks).where(db.tasks.c.kind == "template_import")).mappings().one()
    await run_task(claim("import-test", import_task["id"]))
    batch = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    assert batch["items"][0]["file_id"]
    with db.engine.begin() as conn:
        item = db.one(conn, db.template_import_items, UUID(batch["items"][0]["id"]))
        db.update(conn, db.files, item["file_id"], status="failed", error_code="TEST_FAILURE")
        db.update(conn, db.tasks, item["task_id"], status="failed")
        from study_trail.templates import refresh_imports
        refresh_imports(conn, item["file_id"])
    failed = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    assert failed["items"][0]["can_retry"]
    assert client.get(f"/api/v1/agents/{own['agent_id']}").status_code == 200
    retried = write(client, "POST", f"/api/v1/template-imports/{own['import_batch_id']}/retry", {"item_ids": [batch["items"][0]["id"]]})
    assert retried.status_code == 202, retried.text
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.agents).where(db.agents.c.id == UUID(own["agent_id"]))) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.files).where(db.files.c.id == item["file_id"])) == 1


async def test_import_completion_maps_personal_sources_and_deleted_files_need_confirmation(admin, client, student, monkeypatch):
    from study_trail import providers
    from study_trail.templates import refresh_imports

    from .test_workflow import FakeGateway
    monkeypatch.setattr(providers, "Gateway", FakeGateway)
    _template, version, _ = await published_template(admin)
    own = write(client, "POST", "/api/v1/template-adoptions", {
        "version_id": version["id"], "import_target": {"new_kb_name": "成功导入资料"}}).json()["data"]
    original = client.get(f"/api/v1/plans/{own['plan_ids'][0]}").json()["data"]
    old_source = original["sources"][0]["id"]
    with db.engine.connect() as conn:
        coordinator = conn.execute(sa.select(db.tasks).where(db.tasks.c.kind == "template_import")).mappings().one()
    await run_task(claim("complete-coordinator", coordinator["id"]))
    batch = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    with db.engine.connect() as conn:
        item = db.one(conn, db.template_import_items, UUID(batch["items"][0]["id"]))
    await run_task(claim("complete-file", item["task_id"]))
    completed = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    assert completed["status"] == "completed", completed
    personal = client.get(f"/api/v1/plans/{own['plan_ids'][0]}").json()["data"]
    assert personal["sources"][0]["status"] == "available"
    assert personal["sources"][0]["id"] != old_source
    assert client.get(f"/api/v1/sources/{old_source}/preview").status_code == 409
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.agent_knowledge_bases).where(
            db.agent_knowledge_bases.c.agent_id == UUID(own["agent_id"]))) == 1
        # The immutable adoption revision keeps its original placeholder.
        first = conn.execute(sa.select(db.learning_plan_revisions).where(
            db.learning_plan_revisions.c.plan_id == UUID(own["plan_ids"][0]),
            db.learning_plan_revisions.c.content_version == 1)).mappings().one()
        assert old_source in str(first["payload"])
    assert client.delete(f"/api/v1/files/{item['file_id']}", headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    with db.engine.begin() as conn:
        refresh_imports(conn, batch_id=UUID(own["import_batch_id"]))
    tombstone = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    assert tombstone["items"][0]["status"] == "deleted"
    retry = write(client, "POST", f"/api/v1/template-imports/{own['import_batch_id']}/retry", {
        "item_ids": [item["id"].hex]})
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "IMPORT_ITEM_DELETED"
    blocked = write(client, "POST", f"/api/v1/template-adoptions/{own['id']}/imports", {
        "target_kb_id": str(item["target_kb_id"])})
    assert blocked.status_code == 409
    confirmed = write(client, "POST", f"/api/v1/template-adoptions/{own['id']}/imports", {
        "target_kb_id": str(item["target_kb_id"]), "confirm_reimport": True})
    assert confirmed.status_code == 202, confirmed.text


async def test_template_draft_metadata_is_not_published_early(admin, client, student):
    template, version, _ = await published_template(admin)
    created = write(admin, "POST", f"/api/admin/agent-templates/{template['id']}/drafts", {
        "expected_version": template["lock_version"]})
    assert created.status_code == 201, created.text
    draft = created.json()["data"]
    saved = write(admin, "PUT", f"/api/admin/template-versions/{draft['id']}", {
        "expected_version": draft["lock_version"], "payload": draft["payload"], "name": "新版名称", "description": "新版描述"})
    assert saved.status_code == 200, saved.text
    still_old = client.get(f"/api/v1/agent-templates/{template['id']}/versions/{version['id']}").json()["data"]
    assert still_old["name"] == "向量模板"
    assert saved.json()["data"]["payload"]["name"] == "新版名称"
    published = write(admin, "POST", f"/api/admin/template-versions/{draft['id']}/publish", {
        "expected_version": saved.json()["data"]["lock_version"]})
    assert published.status_code == 200, published.text
    assert published.json()["data"]["name"] == "新版名称"


async def test_import_coordinator_failure_leaves_waiting_items_retryable(admin, client, student):
    from study_trail.execution import terminal
    _template, version, _ = await published_template(admin)
    own = write(client, "POST", "/api/v1/template-adoptions", {
        "version_id": version["id"], "import_target": {"new_kb_name": "中断后重试"}}).json()["data"]
    with db.engine.begin() as conn:
        coordinator = conn.execute(sa.select(db.tasks).where(db.tasks.c.kind == "template_import")).mappings().one()
        terminal(conn, coordinator, "failed", code="WORKER_LOST", message="合成中断")
    failed = client.get(f"/api/v1/template-imports/{own['import_batch_id']}").json()["data"]
    assert failed["status"] == "failed"
    assert failed["items"][0]["can_retry"]
    assert failed["items"][0]["error_code"] == "WORKER_LOST"
    assert client.get(f"/api/v1/agents/{own['agent_id']}").status_code == 200
    # Asset parsing/import coordination are file work, never extra user AI rounds.
    report = admin.get("/api/admin/reports/tasks").json()["data"]
    assert report["total_tasks"] == 0 and report["total_role_runs"] == 0
    file_runs = admin.get("/api/admin/file-runs").json()["data"]["items"]
    assert {"template_asset_process", "template_import"}.issubset({x["kind"] for x in file_runs})
