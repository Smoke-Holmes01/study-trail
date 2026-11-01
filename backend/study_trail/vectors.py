"""Chroma is a derived index. MySQL alone authorizes and activates its records."""
import hashlib
import json
from datetime import timedelta
from functools import lru_cache
from uuid import UUID, uuid4, uuid5

import chromadb
import httpx
import sqlalchemy as sa
from chromadb.config import Settings as ChromaSettings

from . import db
from .config import settings

DIMENSIONS = 1024


@lru_cache(maxsize=8)
def _client(host, port):
    # Probe before the pinned client's constructor, which performs identity calls.
    httpx.get(f"http://{host}:{port}/api/v2/heartbeat", timeout=5).raise_for_status()
    client = chromadb.HttpClient(host=host, port=port, settings=ChromaSettings(anonymized_telemetry=False))
    # chromadb-client 1.5.9 otherwise sets timeout=None. Keep transport bounded.
    client._server._session.timeout = httpx.Timeout(15)
    return client


def client():
    cfg = settings()
    return _client(cfg.chroma_host, cfg.chroma_port)


def collection(create=False):
    cfg = settings()
    if create:
        result = client().get_or_create_collection(
            cfg.chroma_collection, embedding_function=None,
            metadata={"study_trail_schema": "v004", "dimensions": DIMENSIONS},
            configuration={"hnsw": {"space": "cosine"}},
        )
    else:
        result = client().get_collection(cfg.chroma_collection, embedding_function=None)
    if (result.metadata or {}).get("study_trail_schema") != "v004" or (result.metadata or {}).get("dimensions") != DIMENSIONS:
        raise RuntimeError("Incompatible Chroma collection identity")
    if result.configuration.get("hnsw", {}).get("space") != "cosine":
        raise RuntimeError("Chroma collection must use cosine distance")
    return result


def content_hash(chunks):
    value = [(str(c["id"]), hashlib.sha256(c["content_text"].encode()).hexdigest()) for c in chunks]
    return hashlib.sha256(json.dumps(sorted(value), separators=(",", ":")).encode()).hexdigest()


def prepare(conn, file, chunks, task=None, parse_version=None):
    ident = uuid4()
    prepared = [{**chunk, "id": chunk.get("id") or uuid5(ident, str(i))} for i, chunk in enumerate(chunks)]
    row = dict(id=ident, owner_id=file["owner_id"], file_id=file["id"],  # noqa: C408
               knowledge_base_id=file["knowledge_base_id"],
               task_id=task["id"] if task else None, claim_token=task["claim_token"] if task else None,
               parse_version=parse_version or file["parse_version"], index_fingerprint=file["index_fingerprint"],
               expected_count=len(prepared), content_hash=content_hash(prepared), state="staging")
    conn.execute(db.vector_generations.insert().values(**row))
    return row, prepared


def upsert(generation, chunks, embeddings):
    if len(chunks) != len(embeddings) or any(len(v) != DIMENSIONS for v in embeddings):
        raise ValueError("Invalid embedding batch")
    embeddings = [[float(value) for value in vector] for vector in embeddings]
    target = collection()
    for start in range(0, len(chunks), 32):
        batch = chunks[start:start + 32]
        meta = [{"owner_id": str(generation["owner_id"]), "knowledge_base_id": str(generation["knowledge_base_id"]),
                 "file_id": str(generation["file_id"]), "parse_version": generation["parse_version"],
                 "index_fingerprint": generation["index_fingerprint"], "generation_id": str(generation["id"]),
                 "chunk_id": str(c["id"]), "content_hash": hashlib.sha256(c["content_text"].encode()).hexdigest()}
                for c in batch]
        target.upsert(ids=[f"{generation['id']}:{c['id']}" for c in batch],
                      embeddings=embeddings[start:start + 32], metadatas=meta)
    verify_generation(generation, chunks)


def verify_generation(generation, chunks):
    result = collection().get(where={"generation_id": str(generation["id"])}, include=["metadatas", "embeddings"])
    expected = {f"{generation['id']}:{c['id']}": c for c in chunks}
    if set(result["ids"]) != set(expected) or len(result["ids"]) != generation["expected_count"]:
        raise RuntimeError("Chroma generation records are incomplete")
    for i, ident in enumerate(result["ids"]):
        meta = result["metadatas"][i]
        c = expected[ident]
        if len(result["embeddings"][i]) != DIMENSIONS or any(
            meta.get(key) != value for key, value in {
                "owner_id": str(generation["owner_id"]), "file_id": str(generation["file_id"]),
                "knowledge_base_id": str(generation["knowledge_base_id"]),
                "generation_id": str(generation["id"]), "chunk_id": str(c["id"]),
                "parse_version": generation["parse_version"], "index_fingerprint": generation["index_fingerprint"],
                "content_hash": hashlib.sha256(c["content_text"].encode()).hexdigest(),
            }.items()
        ):
            raise RuntimeError("Chroma generation identity mismatch")
    if content_hash(chunks) != generation["content_hash"]:
        raise RuntimeError("MySQL chunk digest mismatch")


def retire(conn, *, file_ids=None, task=None, generation_id=None):
    clauses = [db.vector_generations.c.state != "retired"]
    if file_ids is not None:
        clauses.append(db.vector_generations.c.file_id.in_(file_ids))
    elif task is not None:
        clauses.extend([db.vector_generations.c.task_id == task["id"],
                        db.vector_generations.c.claim_token == task["claim_token"],
                        db.vector_generations.c.state == "staging"])
    elif generation_id is not None:
        clauses.append(db.vector_generations.c.id == generation_id)
    else:
        raise ValueError("Explicit retirement identity required")
    conn.execute(db.vector_generations.update().where(*clauses).values(
        state="retired", retired_at=db.now(), next_cleanup_at=db.now()))


def activate(conn, file, generation):
    current = conn.execute(sa.select(db.vector_generations).where(
        db.vector_generations.c.id == generation["id"]).with_for_update()).mappings().one()
    if current["state"] != "staging" or current["index_fingerprint"] != file["index_fingerprint"]:
        raise RuntimeError("Index generation was retired or configuration changed")
    if file["active_generation_id"]:
        retire(conn, generation_id=file["active_generation_id"])
    conn.execute(db.vector_generations.update().where(db.vector_generations.c.id == generation["id"]).values(state="active"))
    db.update(conn, db.files, file["id"], active_generation_id=generation["id"])


def retrieve(owner, bases, fingerprint, embedding):
    with db.engine.connect() as conn:
        active = list(conn.execute(sa.select(db.files.c.active_generation_id).where(
            db.files.c.owner_id == owner, db.files.c.knowledge_base_id.in_(bases),
            db.files.c.status == "ready", db.files.c.index_fingerprint == fingerprint,
            db.files.c.active_generation_id.is_not(None))).scalars())
    if not active:
        return []
    where = {"$and": [{"owner_id": str(owner)}, {"knowledge_base_id": {"$in": [str(b) for b in bases]}},
                      {"index_fingerprint": fingerprint}, {"generation_id": {"$in": [str(g) for g in active]}}]}
    target = collection()
    rows = []
    for count in (20, 40, 80, 160, 320):
        found = target.query(query_embeddings=[embedding], n_results=count, where=where, include=["metadatas"])
        identities = []
        for record, meta in zip(found["ids"][0], found["metadatas"][0], strict=True):
            try:
                gen, chunk = record.split(":", 1)
                if meta["generation_id"] == gen and meta["chunk_id"] == chunk:
                    identities.append((UUID(chunk), UUID(gen)))
            except (ValueError, KeyError, TypeError):
                continue
        with db.engine.connect() as conn:
            valid = conn.execute(sa.select(db.document_chunks, db.files.c.original_name, db.files.c.active_generation_id)
                .join(db.files, db.files.c.id == db.document_chunks.c.file_id)
                .join(db.vector_generations, db.vector_generations.c.id == db.files.c.active_generation_id)
                .where(db.document_chunks.c.id.in_([i[0] for i in identities]),
                       db.document_chunks.c.owner_id == owner, db.files.c.owner_id == owner,
                       db.document_chunks.c.knowledge_base_id.in_(bases), db.files.c.status == "ready",
                       db.files.c.parse_version == db.document_chunks.c.parse_version,
                       db.document_chunks.c.index_fingerprint == fingerprint, db.files.c.index_fingerprint == fingerprint,
                       db.vector_generations.c.state == "active")).mappings().all()
        by_id = {(r["id"], r["active_generation_id"]): dict(r) for r in valid}
        rows = [by_id[i] for i in identities if i in by_id][:20]
        if len(rows) >= 20 or len(found["ids"][0]) < count:
            break
    if not found["ids"][0]:
        raise RuntimeError("Ready files have no indexed vectors; run vector_index check/rebuild")
    return rows


def cleanup_one():
    claim = uuid4()
    with db.engine.begin() as conn:
        row = conn.execute(sa.select(db.vector_generations).where(
            db.vector_generations.c.state == "retired", db.vector_generations.c.next_cleanup_at <= db.now(),
            sa.or_(db.vector_generations.c.cleanup_lease_at.is_(None), db.vector_generations.c.cleanup_lease_at < db.now()))
            .order_by(db.vector_generations.c.next_cleanup_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
        if row is None:
            return False
        conn.execute(db.vector_generations.update().where(db.vector_generations.c.id == row["id"]).values(
            cleanup_claim=claim, cleanup_lease_at=db.now() + timedelta(seconds=60)))
    error = None
    try:
        collection().delete(where={"$and": [{"generation_id": str(row["id"])}, {"owner_id": str(row["owner_id"])}]})
    except Exception as exc:  # noqa: BLE001 - persist cleanup failures and retry without dropping tombstones
        error = type(exc).__name__[:80]
    attempts = row["cleanup_attempts"] + 1 if error else 0
    delay = min(3600, 60 * 2 ** min(attempts - 1, 6)) if error else 60 if db.now() - row["retired_at"] < timedelta(minutes=10) else 86400
    with db.engine.begin() as conn:
        conn.execute(db.vector_generations.update().where(db.vector_generations.c.id == row["id"],
            db.vector_generations.c.cleanup_claim == claim).values(cleanup_claim=None, cleanup_lease_at=None,
            cleanup_attempts=attempts, last_error=error, next_cleanup_at=db.now() + timedelta(seconds=delay)))
    return True


def recover_staging():
    """Retire abandoned task attempts, never a file's existing active generation."""
    with db.engine.begin() as conn:
        rows = conn.execute(sa.select(db.vector_generations).where(
            db.vector_generations.c.state == "staging").with_for_update(skip_locked=True)).mappings().all()
        for generation in rows:
            task = db.one(conn, db.tasks, generation["task_id"]) if generation["task_id"] else None
            abandoned = generation["task_id"] is not None and (
                not task or task["status"] != "running" or task["claim_token"] != generation["claim_token"]
                or task["lease_expires_at"] <= db.now())
            # CLI rebuilds run under a deployment maintenance lock; a crashed
            # command's staging generation expires after the maximum task budget.
            abandoned |= generation["task_id"] is None and generation["created_at"] < db.now() - timedelta(seconds=3660)
            if abandoned:
                retire(conn, generation_id=generation["id"])
