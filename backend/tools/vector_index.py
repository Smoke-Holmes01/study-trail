"""Derived-index maintenance. Mutations require --apply; rebuild preserves source IDs."""
import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa

from study_trail import db, platform, vectors
from study_trail.providers import Gateway


def check():
    vectors.collection()
    checked = 0
    with db.engine.connect() as conn:
        files = conn.execute(sa.select(db.files).where(db.files.c.status == "ready")).mappings().all()
        for file in files:
            generation = db.one(conn, db.vector_generations, file["active_generation_id"])
            if not generation or generation["state"] != "active" or generation["file_id"] != file["id"] or generation["owner_id"] != file["owner_id"] or generation["parse_version"] != file["parse_version"] or generation["index_fingerprint"] != file["index_fingerprint"]:
                raise RuntimeError("Ready file has no compatible active generation")
            chunks = conn.execute(sa.select(db.document_chunks).where(db.document_chunks.c.file_id == file["id"], db.document_chunks.c.parse_version == file["parse_version"])).mappings().all()
            vectors.verify_generation(generation, chunks)
            checked += len(chunks)
        known = {str(ident) for ident in conn.execute(sa.select(db.vector_generations.c.id)).scalars()}
    unknown = unknown_records(known)
    return {"ready_files": len(files), "verified_chunks": checked, "unknown_vector_records": len(unknown)}


def unknown_records(known):
    target = vectors.collection()
    result = []
    offset = 0
    while True:
        batch = target.get(limit=500, offset=offset, include=["metadatas"])
        result.extend(ident for ident, meta in zip(batch["ids"], batch["metadatas"], strict=True)
                      if not meta or meta.get("generation_id") not in known)
        if len(batch["ids"]) < 500:
            return result
        offset += 500


async def rebuild(file_id):
    with db.engine.begin() as conn:
        platform.lock(conn)
        file = db.one(conn, db.files, file_id, lock=True)
        if not file or file["status"] != "ready":
            raise RuntimeError("Rebuild requires an existing ready file")
        chunks = [dict(c) for c in conn.execute(sa.select(db.document_chunks).where(
            db.document_chunks.c.file_id == file_id, db.document_chunks.c.parse_version == file["parse_version"]).order_by(db.document_chunks.c.chunk_index)).mappings()]
        if not chunks:
            raise RuntimeError("MySQL chunks are missing; refusing to replace historical source IDs")
        frozen = platform.snapshot(conn, generation_required=False)
        if not frozen.get("embedding") or frozen["embedding"]["index_fingerprint"] != file["index_fingerprint"]:
            raise RuntimeError("The original compatible embedding configuration is required")
        generation, chunks = vectors.prepare(conn, file, chunks)
    task = {"id": None, "kind": "file_process", "owner_id": file["owner_id"],
            "deadline_at": db.now() + timedelta(seconds=3600), "config_snapshot": frozen}
    try:
        gateway = Gateway(task)
        embeddings = []
        for start in range(0, len(chunks), 32):
            embeddings.extend(await gateway.embed([c["content_text"] for c in chunks[start:start + 32]]))
        await asyncio.to_thread(vectors.upsert, generation, chunks, embeddings)
        with db.engine.begin() as conn:
            current = db.one(conn, db.files, file_id, lock=True)
            live_chunks = conn.execute(sa.select(db.document_chunks).where(db.document_chunks.c.file_id == file_id)).mappings().all()
            if not current or current["parse_version"] != file["parse_version"] or vectors.content_hash(live_chunks) != generation["content_hash"]:
                raise RuntimeError("File changed during rebuild")
            vectors.activate(conn, current, generation)
    except BaseException:
        with db.engine.begin() as conn:
            vectors.retire(conn, generation_id=generation["id"])
        raise
    return {"file_id": str(file_id), "rebuilt_chunks": len(chunks), "source_ids_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "check", "cleanup", "rebuild"])
    parser.add_argument("--apply", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file-id", type=UUID)
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.command == "check":
        result = check()
    elif not args.apply:
        with db.engine.connect() as conn:
            count = conn.scalar(sa.select(sa.func.count()).select_from(db.files).where(db.files.c.status == "ready"))
        result = {"dry_run": True, "command": args.command, "ready_files": count,
                  "requires_apply": True, "embedding_calls_on_rebuild": args.command == "rebuild"}
    elif args.command == "init":
        vectors.collection(create=True)
        result = {"initialized": True}
    else:
        from tools.storage_maintenance import offline_maintenance
        with offline_maintenance():
            if args.command == "cleanup":
                vectors.recover_staging()
                count = 0
                while vectors.cleanup_one():
                    count += 1
                with db.engine.connect() as conn:
                    known = {str(ident) for ident in conn.execute(sa.select(db.vector_generations.c.id)).scalars()}
                unknown = unknown_records(known)
                for start in range(0, len(unknown), 500):
                    vectors.collection().delete(ids=unknown[start:start + 500])
                result = {"cleaned_generations": count, "removed_unknown_records": len(unknown)}
            else:
                if not args.file_id and not args.all:
                    parser.error("rebuild requires --file-id or --all")
                with db.engine.connect() as conn:
                    ids = [args.file_id] if args.file_id else list(conn.execute(sa.select(db.files.c.id).where(db.files.c.status == "ready")).scalars())
                vectors.collection(create=True)
                result = [asyncio.run(rebuild(ident)) for ident in ids]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
