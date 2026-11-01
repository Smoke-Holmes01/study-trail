"""Explicit real-Chroma fixtures; production db.insert never performs vector I/O."""
import sqlalchemy as sa

from study_trail import db, vectors


def indexed_chunk(conn, table, *, embedding, **values):
    assert table is db.document_chunks
    row = db.insert(conn, table, **values)
    file = db.one(conn, db.files, values["file_id"])
    chunks = list(conn.execute(sa.select(table).where(table.c.file_id == file["id"])).mappings())
    old = {}
    if file["active_generation_id"]:
        generation = dict(db.one(conn, db.vector_generations, file["active_generation_id"]))
        existing = vectors.collection().get(where={"generation_id": str(generation["id"])}, include=["embeddings", "metadatas"])
        old = {meta["chunk_id"]: vector for meta, vector in zip(existing["metadatas"], existing["embeddings"], strict=True)}
        generation.update(expected_count=len(chunks), content_hash=vectors.content_hash(chunks))
        conn.execute(db.vector_generations.update().where(db.vector_generations.c.id == generation["id"]).values(expected_count=generation["expected_count"], content_hash=generation["content_hash"]))
    else:
        generation, chunks = vectors.prepare(conn, file, chunks)
    old[str(row["id"])] = embedding
    vectors.upsert(generation, chunks, [old[str(c["id"])] for c in chunks])
    if not file["active_generation_id"]:
        vectors.activate(conn, file, generation)
    return row
