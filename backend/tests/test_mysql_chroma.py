"""Real MySQL/Chroma regression coverage for the v004 storage boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from study_trail import db, platform, vectors
from study_trail.execution import claim, run_task
from study_trail.providers import Gateway
from tools.vector_index import check, rebuild

from .indexing import indexed_chunk
from .test_api import post

VECTOR = [1.0] + [0.0] * 1023


def seed(client, resource):
    owner = UUID(resource["student"]["id"])
    kb = client.post("/api/v1/knowledge-bases", json={"name": "迁移回归资料"}).json()["data"]
    with db.engine.begin() as conn:
        file = db.insert(conn, db.files, owner_id=owner, knowledge_base_id=UUID(kb["id"]),
            original_name="教材.txt", extension=".txt", media_type="text/plain", storage_key="test/" + uuid4().hex,
            sha256=b"a" * 32, byte_size=30, status="ready", text_snapshot="矩阵是二维数组。")
        chunk = indexed_chunk(conn, db.document_chunks, owner_id=owner, knowledge_base_id=UUID(kb["id"]),
            file_id=file["id"], parse_version=1, chunk_index=0, content_text="矩阵是二维数组。",
            char_start=0, char_end=8, locator={"line_start": 1}, embedding=VECTOR)
        file = dict(db.one(conn, db.files, file["id"]))
    return file, dict(chunk)


def test_mysql_baseline_and_constraints():
    with db.engine.connect() as conn:
        assert conn.dialect.name == "mysql"
        assert len(db.metadata.tables) == 44
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "mysql_0002"
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.runtime_locks)) == 258
        assert "embedding" not in db.document_chunks.c
        assert conn.exec_driver_sql("SELECT @@transaction_isolation").scalar() == "READ-COMMITTED"


def test_case_and_trailing_space_idempotency_keys_are_distinct(client, student):
    ids = []
    for key in ["K", "k", "k "]:
        response = post(client, "/prompt-tasks", {"mode": "generate", "name": "学习助手"}, key)
        assert response.status_code == 202, response.text
        ids.append(response.json()["data"]["task"]["id"])
    assert len(set(ids)) == 3
    assert post(client, "/prompt-tasks", {"mode": "generate", "name": "学习助手"}, "k ").json()["data"]["task"]["id"] == ids[-1]


def test_concurrent_same_key_replays_one_accepted_task(client, resource):
    key = str(uuid4())
    path = f"/conversations/{resource['conversation']['id']}/messages"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: post(client, path, {"content_text": "解释矩阵"}, key), range(2)))
    assert [r.status_code for r in results] == [202, 202]
    assert results[0].json()["data"] == results[1].json()["data"]
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.tasks)) == 1
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.runtime_locks)) == 258


def test_initial_config_failure_rolls_back_and_pointer_cannot_clear():
    with db.engine.connect() as conn:
        count = conn.scalar(sa.select(sa.func.count()).select_from(db.platform_configs))
    with pytest.raises(platform.Problem), db.engine.begin() as conn:
        platform.create(conn, "skill", {"stable_id": "bad-payload", "name": "错误技能", "payload": {}}, None)
    with db.engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.platform_configs)) == count
        config = conn.execute(sa.select(db.platform_configs)).mappings().first()
    with pytest.raises(sa.exc.DBAPIError), db.engine.begin() as conn:
        conn.execute(db.platform_configs.update().where(db.platform_configs.c.id == config["id"]).values(current_revision_id=None))


@pytest.mark.parametrize("table", ["config_revisions", "credential_versions", "resource_events", "admin_operation_logs"])
def test_history_update_and_delete_remain_forbidden(table, client, resource):
    target = db.metadata.tables[table]
    with db.engine.connect() as conn:
        row = conn.execute(sa.select(target)).mappings().first()
    if table == "admin_operation_logs" and row is None:
        with db.engine.begin() as conn:
            conn.execute(target.insert().values(actor_id=UUID(resource["student"]["id"]), action="test", target_kind="system", result="success", request_id=uuid4()))
        with db.engine.connect() as conn:
            row = conn.execute(sa.select(target)).mappings().first()
    assert row is not None
    for statement in [target.delete(), target.update().values(id=row["id"])]:
        with pytest.raises(sa.exc.DBAPIError), db.engine.begin() as conn:
            conn.execute(statement.where(target.c.id == row["id"]))


async def test_rebuild_preserves_chunk_and_source_ids_and_failure_keeps_old(client, resource, monkeypatch):
    file, chunk = seed(client, resource)
    with db.engine.begin() as conn:
        source = db.insert(conn, db.sources, owner_id=file["owner_id"], kind="file", file_id=file["id"], chunk_id=chunk["id"], title_snapshot="教材", excerpt_snapshot=chunk["content_text"], locator_snapshot=chunk["locator"])
    async def embed(self, texts):
        return [VECTOR for _ in texts]
    monkeypatch.setattr(Gateway, "embed", embed)
    result = await rebuild(file["id"])
    assert result["source_ids_preserved"]
    with db.engine.connect() as conn:
        active = db.one(conn, db.files, file["id"])["active_generation_id"]
        assert active != file["active_generation_id"]
        assert db.one(conn, db.sources, source["id"])["chunk_id"] == chunk["id"]
        assert db.one(conn, db.vector_generations, file["active_generation_id"])["state"] == "retired"
    assert check()["verified_chunks"] == 1
    async def failed(self, texts):
        raise RuntimeError("synthetic embedding failure")
    monkeypatch.setattr(Gateway, "embed", failed)
    with pytest.raises(RuntimeError):
        await rebuild(file["id"])
    with db.engine.connect() as conn:
        assert db.one(conn, db.files, file["id"])["active_generation_id"] == active
        assert db.one(conn, db.sources, source["id"])["chunk_id"] == chunk["id"]


def test_late_upsert_is_invisible_and_retired_generation_is_rescanned(client, resource):
    file, chunk = seed(client, resource)
    with db.engine.begin() as conn:
        old = dict(db.one(conn, db.vector_generations, file["active_generation_id"]))
        generation, chunks = vectors.prepare(conn, file, [chunk])
    vectors.upsert(generation, chunks, [VECTOR])
    with db.engine.begin() as conn:
        vectors.activate(conn, db.one(conn, db.files, file["id"], lock=True), generation)
    assert vectors.cleanup_one()
    vectors.upsert(old, [chunk], [VECTOR])  # server completes the old HTTP request late
    rows = vectors.retrieve(file["owner_id"], [file["knowledge_base_id"]], file["index_fingerprint"], VECTOR)
    assert len(rows) == 1 and rows[0]["active_generation_id"] == generation["id"]
    with db.engine.begin() as conn:
        conn.execute(db.vector_generations.update().where(db.vector_generations.c.id == old["id"]).values(next_cleanup_at=db.now()))
    assert vectors.cleanup_one()
    assert vectors.collection().get(where={"generation_id": str(old["id"])})["ids"] == []
    with db.engine.connect() as conn:
        assert db.one(conn, db.vector_generations, old["id"])["state"] == "retired"


@pytest.mark.parametrize("lose_claim", [False, True])
async def test_external_write_then_failure_or_lost_lease_never_activates(client, resource, monkeypatch, lose_claim):
    async def embed(self, texts):
        return [VECTOR for _ in texts]
    monkeypatch.setattr(Gateway, "embed", embed)
    kb = client.post("/api/v1/knowledge-bases", json={"name": "中断测试"}).json()["data"]
    response = client.post(f"/api/v1/knowledge-bases/{kb['id']}/files", files={"files": ("资料.txt", "矩阵知识".encode(), "text/plain")}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 202, response.text
    task = claim("test")
    original = vectors.upsert
    def interrupted(generation, chunks, embeddings):
        original(generation, chunks, embeddings)
        if lose_claim:
            with db.engine.begin() as conn:
                db.update(conn, db.tasks, task["id"], lease_expires_at=db.now() - timedelta(seconds=1))
        else:
            raise RuntimeError("synthetic failure after external write")
    monkeypatch.setattr(vectors, "upsert", interrupted)
    await run_task(task)
    with db.engine.connect() as conn:
        file = db.one(conn, db.files, task["file_id"])
        assert file["status"] != "ready" and file["active_generation_id"] is None
        generations = conn.execute(sa.select(db.vector_generations)).mappings().all()
        assert generations and all(g["state"] == "retired" for g in generations)


def test_deleted_file_and_forged_candidates_cannot_be_retrieved(client, resource):
    file, _chunk = seed(client, resource)
    assert vectors.retrieve(file["owner_id"], [file["knowledge_base_id"]], file["index_fingerprint"], VECTOR)
    with db.engine.begin() as conn:
        vectors.retire(conn, file_ids=[file["id"]])
        conn.execute(db.files.delete().where(db.files.c.id == file["id"]))
    assert vectors.retrieve(file["owner_id"], [file["knowledge_base_id"]], file["index_fingerprint"], VECTOR) == []
    assert vectors.collection().count() == 1  # SQL authorization takes effect before asynchronous physical deletion


def test_missing_chroma_vectors_are_reported_as_unavailable(client, resource):
    file, _ = seed(client, resource)
    vectors.collection().delete(where={"generation_id": str(file["active_generation_id"])})
    with pytest.raises(RuntimeError, match="no indexed vectors"):
        vectors.retrieve(file["owner_id"], [file["knowledge_base_id"]], file["index_fingerprint"], VECTOR)
    with pytest.raises(RuntimeError, match="incomplete"):
        check()


def test_invalid_candidates_are_bounded_at_320_without_relaxing_authorization(client, resource, monkeypatch):
    file, chunk = seed(client, resource)
    target = vectors.collection()
    # Simulate SQL records lost after a partial restore. Every vector remains
    # unauthorized, regardless of the approximate index's distance ordering.
    with db.engine.begin() as conn:
        conn.execute(db.document_chunks.delete().where(db.document_chunks.c.id == chunk["id"]))
    ids = [str(uuid4()) for _ in range(321)]
    target.upsert(ids=[f"{file['active_generation_id']}:{ident}" for ident in ids], embeddings=[VECTOR] * len(ids),
        metadatas=[{"owner_id": str(file["owner_id"]), "knowledge_base_id": str(file["knowledge_base_id"]),
            "file_id": str(file["id"]), "parse_version": 1, "index_fingerprint": file["index_fingerprint"],
            "generation_id": str(file["active_generation_id"]), "chunk_id": ident} for ident in ids])
    calls = []
    class ObservedCollection:
        def query(self, **kwargs):
            calls.append(kwargs["n_results"])
            return target.query(**kwargs)
    monkeypatch.setattr(vectors, "collection", lambda: ObservedCollection())
    assert vectors.retrieve(file["owner_id"], [file["knowledge_base_id"]], file["index_fingerprint"], VECTOR) == []
    assert calls == [20, 40, 80, 160, 320]


def test_multi_worker_capacity_is_enforced_under_concurrent_claims(client, student):
    for _ in range(4):
        assert post(client, "/prompt-tasks", {"mode": "generate", "name": "并发测试"}).status_code == 202
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda i: claim(f"parallel-{i}"), range(4)))
    assert sum(row is not None for row in claimed) == 2
    assert len({row["id"] for row in claimed if row}) == 2


def test_backup_refuses_unfinished_tasks_without_worker_and_retains_maintenance(client, resource, monkeypatch, tmp_path):
    from tools import storage_maintenance as maintenance
    assert post(client, f"/conversations/{resource['conversation']['id']}/messages", {"content_text": "排空测试"}).status_code == 202
    monkeypatch.setattr(maintenance, "LOCAL", tmp_path)
    monkeypatch.setattr(maintenance, "running", list)
    monkeypatch.setattr(maintenance, "writer_processes", list)
    monkeypatch.setattr(maintenance, "stop", lambda *args: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("No database service may stop before tasks drain")
    monkeypatch.setattr(maintenance, "compose", forbidden)
    # Only the name guard is adapted; all SQL reads still use the isolated test DB.
    class AllowedEngine:
        url = type("URL", (), {"database": "study_trail"})()
        def connect(self):
            return original.connect()
    original = db.engine
    monkeypatch.setattr(db, "engine", AllowedEngine())
    with pytest.raises(RuntimeError, match="did not drain"):
        maintenance.backup(timeout=1)
    assert (tmp_path / "maintenance").exists()
    assert not (tmp_path / "backups").exists()
