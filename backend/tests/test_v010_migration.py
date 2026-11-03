"""A populated frozen mysql_0001 upgrades without rewriting legacy learning data."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from dotenv import dotenv_values

from study_trail import db, platform, vectors
from study_trail.config import settings


def test_populated_mysql_0001_upgrade_preserves_references_credentials_and_revisions(monkeypatch):
    runtime = settings().runtime_root.resolve()
    if runtime.name != "v010-verify":
        raise RuntimeError("Migration rehearsal requires the dedicated v010-verify deployment")
    infra = dotenv_values(runtime / "infra.env")
    name = "study_trail_migration_" + uuid4().hex[:12]
    assert re.fullmatch(r"study_trail_migration_[0-9a-f]{12}", name)
    root_url = db.engine.url.set(username="root", password=infra["MYSQL_ROOT_PASSWORD"], database=None)
    server = sa.create_engine(root_url, isolation_level="AUTOCOMMIT")
    backend = Path(__file__).parents[1]
    env = {**os.environ, "DATABASE_URL": root_url.set(database=name).render_as_string(hide_password=False),
        "CHROMA_COLLECTION": name + "_chunks", "STORAGE_ROOT": str(runtime / name / "storage")}
    def migrate(target):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", target], cwd=backend, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    engine = None
    vector_client = vectors.client()
    target = vector_client.create_collection(name + "_chunks", embedding_function=None,
        metadata={"study_trail_schema": "v004", "dimensions": 1024}, configuration={"hnsw": {"space": "cosine"}})
    monkeypatch.setattr(vectors, "collection", lambda *args, **kwargs: target)
    private_file = runtime / name / "storage" / "legacy.txt"
    private_file.parent.mkdir(parents=True, exist_ok=True)
    private_file.write_bytes("旧文件正文保留".encode())
    original_file_hash = hashlib.sha256(private_file.read_bytes()).digest()
    try:
        with server.connect() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
        migrate("mysql_0001")
        engine = sa.create_engine(root_url.set(database=name))
        ids = {x: uuid4().hex for x in ("owner", "agent", "conversation", "message", "exercise", "plan", "source", "config", "credential", "kb", "file", "chunk", "generation")}
        fingerprint = "a" * 64
        chunk = {"id": UUID(ids["chunk"]), "content_text": "旧文件正文保留"}
        generation = {"id": UUID(ids["generation"]), "owner_id": UUID(ids["owner"]), "file_id": UUID(ids["file"]),
            "knowledge_base_id": UUID(ids["kb"]), "parse_version": 1, "index_fingerprint": fingerprint,
            "expected_count": 1, "content_hash": vectors.content_hash([chunk])}
        plan_content = {"goal": "保留旧计划", "deadline": None, "stages": [{"id": uuid4().hex, "title": "旧阶段", "goal": "基础", "knowledge_points": ["向量"],
            "tasks": [{"id": uuid4().hex, "title": "旧任务", "estimated_minutes": 30, "resource_source_ids": [ids["source"]], "exercise_suggestions": []}]}]}
        ciphertext = platform.cipher().encrypt(b"synthetic-upgrade-credential")
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO users(id,login,login_normalized,password_hash,display_name) VALUES(:owner,'migration_owner','migration_owner','synthetic','迁移用户')"), ids)
            conn.execute(sa.text("INSERT INTO knowledge_bases(id,owner_id,name) VALUES(:kb,:owner,'旧个人库')"), ids)
            conn.execute(sa.text("INSERT INTO vector_generations(id,owner_id,file_id,knowledge_base_id,parse_version,index_fingerprint,expected_count,content_hash,state) VALUES(:generation,:owner,:file,:kb,1,:fingerprint,1,:content_hash,'active')"),
                {**ids, "fingerprint": fingerprint, "content_hash": generation["content_hash"]})
            conn.execute(sa.text("INSERT INTO files(id,owner_id,knowledge_base_id,original_name,extension,media_type,storage_key,sha256,byte_size,status,text_snapshot,index_fingerprint,active_generation_id) VALUES(:file,:owner,:kb,'legacy.txt','.txt','text/plain','legacy.txt',:sha,:size,'ready','旧文件正文保留',:fingerprint,:generation)"),
                {**ids, "sha": original_file_hash, "size": private_file.stat().st_size, "fingerprint": fingerprint})
            conn.execute(sa.text("INSERT INTO document_chunks(id,owner_id,knowledge_base_id,file_id,parse_version,chunk_index,content_text,char_start,char_end,locator,index_fingerprint) VALUES(:chunk,:owner,:kb,:file,1,0,'旧文件正文保留',0,7,:locator,:fingerprint)"),
                {**ids, "locator": json.dumps({"line_start": 1}), "fingerprint": fingerprint})
            conn.execute(sa.text("INSERT INTO agents(id,owner_id,name,system_prompt,model_id) VALUES(:agent,:owner,'旧智能体','旧个人提示词','fixture-model')"), ids)
            # MySQL evaluates the generated recent_at expression during this
            # raw legacy insert; supply its timestamp input explicitly.
            conn.execute(sa.text("INSERT INTO conversations(id,owner_id,agent_id,created_at,updated_at) VALUES(:conversation,:owner,:agent,:stamp,:stamp)"),
                {**ids, "stamp": db.now().replace(tzinfo=None)})
            conn.execute(sa.text("INSERT INTO messages(id,owner_id,agent_id,conversation_id,sequence_no,role,origin,content_text,response_status) VALUES(:message,:owner,:agent,:conversation,1,'assistant','assistant','旧消息','succeeded')"), ids)
            conn.execute(sa.text("INSERT INTO sources(id,owner_id,kind,title_snapshot,url_snapshot,excerpt_snapshot,captured_at) VALUES(:source,:owner,'web','旧引用','https://example.invalid/source','旧片段',:stamp)"),
                {**ids, "stamp": db.now().replace(tzinfo=None)})
            conn.execute(sa.text("INSERT INTO exercises(id,owner_id,agent_id,conversation_id,message_id,position,question_text,provenance,source_ids,known_answer_snapshot) VALUES(:exercise,:owner,:agent,:conversation,:message,0,'旧题干','original',:refs,:answer)"),
                {**ids, "refs": json.dumps([ids["source"]]), "answer": json.dumps({"answer_text": "旧答案", "source_ids": [ids["source"]]})})
            conn.execute(sa.text("INSERT INTO learning_plans(id,owner_id,agent_id,name,content) VALUES(:plan,:owner,:agent,'旧计划',:content)"), {**ids, "content": json.dumps(plan_content)})
            conn.execute(sa.text("INSERT INTO plan_sources(plan_id,source_id,owner_id,position,content_version) VALUES(:plan,:source,:owner,0,1)"), ids)
            conn.execute(sa.text("INSERT INTO platform_configs(id,kind,stable_id,name) VALUES(:config,'model','migration-model','旧模型')"), ids)
            conn.execute(sa.text("INSERT INTO credential_versions(id,config_id,slot,version_no,ciphertext,key_id) VALUES(:credential,:config,'api_key',1,:encrypted,:key_id)"),
                {**ids, "encrypted": ciphertext, "key_id": settings().platform_key_id})
            before = {table: [dict(x) for x in conn.execute(sa.text(f"SELECT * FROM {table}")).mappings()]
                for table in ("files", "document_chunks", "vector_generations")}
        vectors.upsert(generation, [chunk], [[1.0] + [0.0] * 1023])
        migrate("head")
        with engine.begin() as conn:
            assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "mysql_0002"
            assert len(set(sa.inspect(conn).get_table_names()) - {"alembic_version"}) == 44
            exercise = conn.execute(sa.text("SELECT * FROM exercises WHERE id=:exercise"), ids).mappings().one()
            assert exercise["question_text"] == "旧题干" and exercise["content_version"] == 1
            assert exercise["origin_kind"] == "generated" and exercise["current_revision_id"]
            revision = conn.execute(sa.text("SELECT payload FROM exercise_revisions WHERE exercise_id=:exercise"), ids).scalar_one()
            assert json.loads(revision)["answer_text"] == "旧答案"
            plan = conn.execute(sa.text("SELECT content FROM learning_plans WHERE id=:plan"), ids).scalar_one()
            assert json.loads(plan) == plan_content
            assert conn.execute(sa.text("SELECT COUNT(*) FROM learning_plan_revisions WHERE plan_id=:plan"), ids).scalar() == 1
            saved = conn.execute(sa.text("SELECT ciphertext FROM credential_versions WHERE id=:credential"), ids).scalar_one()
            assert saved == ciphertext and platform.cipher().decrypt(saved) == b"synthetic-upgrade-credential"
            conn.execute(sa.text("DELETE FROM messages WHERE id=:message"), ids)
            assert conn.execute(sa.text("SELECT message_id FROM exercises WHERE id=:exercise"), ids).scalar_one() is None
            assert conn.execute(sa.text("SELECT COUNT(*) FROM plan_sources WHERE plan_id=:plan"), ids).scalar() == 1
            for table, rows in before.items():
                assert [dict(x) for x in conn.execute(sa.text(f"SELECT * FROM {table}")).mappings()] == rows
            assert hashlib.sha256(private_file.read_bytes()).digest() == original_file_hash
            vectors.verify_generation(generation, [chunk])
            assert target.query(query_embeddings=[[1.0] + [0.0] * 1023], n_results=1)["ids"][0]
    finally:
        if engine:
            engine.dispose()
        with server.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
        server.dispose()
        vector_client.delete_collection(name + "_chunks")
        private_file.unlink(missing_ok=True)
