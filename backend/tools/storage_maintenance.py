"""Quiesced two-store backup and isolated restore verification for v0.0.4."""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet

from study_trail import db, vectors
from study_trail.config import settings
from tools.local_runtime import DOCKER, LOCAL, ROOT, compose, protect
from tools.manage_runtime import matching, running, stop, writer_processes


@contextmanager
def operator_lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / "operator.lock").open("a+b") as stream:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def offline_maintenance():
    with operator_lock():
        (LOCAL / "maintenance").write_text("index maintenance", encoding="utf-8")
        if running():
            raise RuntimeError("Stop managed application processes before index mutation")
        yield
        # Explicit resume performs readiness and full index verification.


def normalized(value):
    if isinstance(value, dict):
        return {str(k): normalized(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(v) for v in value]
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if hasattr(value, "tolist"):
        return normalized(value.tolist())
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def digest(value):
    return hashlib.sha256(json.dumps(normalized(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relational_snapshot(engine, legacy_types=False):
    result = {}
    with engine.connect() as conn:
        for name in sorted(sa.inspect(conn).get_table_names()):
            table = sa.Table(name, sa.MetaData(), autoload_with=conn)
            if legacy_types and name in db.metadata.tables:
                # Older manifests hashed the application's UUID/UTC/Boolean
                # values. Reflect names first so old schemas never select new columns.
                for column in table.c:
                    if column.name in db.metadata.tables[name].c:
                        column.type = db.metadata.tables[name].c[column.name].type
            rows = [normalized(dict(r)) for r in conn.execute(sa.select(table)).mappings()]
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            result[name] = {"count": len(rows), "sha256": digest(rows)}
    return result


def chroma_snapshot(client):
    result = {}
    offset = 0
    while True:
        collections = client.list_collections(limit=100, offset=offset)
        for item in collections:
            collection = client.get_collection(item.name, embedding_function=None)
            records = []
            start = 0
            while True:
                batch = collection.get(limit=500, offset=start, include=["metadatas", "embeddings", "documents"])
                for i, ident in enumerate(batch["ids"]):
                    records.append([ident, batch["metadatas"][i], batch["embeddings"][i], batch["documents"][i]])
                if len(batch["ids"]) < 500:
                    break
                start += 500
            records.sort(key=lambda row: row[0])
            result[item.name] = {"count": len(records), "sha256": digest(records),
                                 "configuration": normalized(collection.configuration), "metadata": collection.metadata}
        if len(collections) < 100:
            break
        offset += 100
    return result


def storage_manifest(root):
    return {str(path.relative_to(root)).replace("\\", "/"): file_hash(path)
            for path in sorted(root.rglob("*")) if path.is_file()} if root.exists() else {}


def backup(timeout=3600, allow_test_source=False):
    source_database = db.engine.url.database
    test_source = allow_test_source and source_database in {"study_trail_test", "study_trail_e2e"}
    if settings().database_url.split(":", 1)[0] != "mysql+pymysql" or (source_database != "study_trail" and not test_source):
        raise RuntimeError("Backup expects the daily-use v004 MySQL database")
    if test_source and settings().chroma_collection != source_database + "_chunks":
        raise RuntimeError("Test rehearsal requires the matching isolated Chroma collection")
    with operator_lock():
        e2e_registry = LOCAL / "e2e-processes.json"
        if e2e_registry.exists():
            for entry in json.loads(e2e_registry.read_text(encoding="utf-8-sig")):
                if matching(entry):
                    raise RuntimeError("Stop isolated E2E writers before a whole-Chroma backup")
        previous = running()
        (LOCAL / "resume-state.json").write_text(json.dumps([entry["name"] for entry in previous]), encoding="utf-8")
        (LOCAL / "maintenance").write_text("coordinated backup", encoding="utf-8")
        stop(["api"])
        if any("study_trail.api:app" in (p.cmdline() or []) for p in writer_processes()):
            raise RuntimeError("Unrecorded API writer remains; maintenance retained, backup refused")
        deadline = time.monotonic() + timeout
        while True:
            with db.engine.connect() as conn:
                pending = conn.scalar(sa.select(sa.func.count()).select_from(db.tasks).where(db.tasks.c.status.in_(["queued", "running"])))
            if not pending:
                break
            if time.monotonic() >= deadline or not any(e["name"] == "worker" for e in running()):
                raise RuntimeError("Tasks did not drain; maintenance retained and backup not completed")
            time.sleep(1)
        stop()
        if running():
            raise RuntimeError("Application writers are still running")
        if writer_processes():
            raise RuntimeError("Unrecorded application writer remains; backup refused")
        compose("stop", "chroma", capture_output=True)
        target = LOCAL / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target.mkdir(parents=True, exist_ok=False)
        protect(target)
        with (target / "mysql.sql").open("wb") as stream:
            compose("exec", "-T", "mysql", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --no-tablespaces --routines --events --triggers --set-gtid-purged=OFF ' + source_database, stdout=stream, stderr=subprocess.PIPE)
        container = compose("ps", "-a", "-q", "chroma", capture_output=True, text=True).stdout.strip()
        if not container or "\n" in container:
            raise RuntimeError("Unexpected Chroma container identity")
        with (target / "chroma-data.tar").open("wb") as stream:
            subprocess.run([DOCKER, "cp", "-a", container + ":/data/.", "-"], stdout=stream, stderr=subprocess.PIPE, check=True)
        storage = settings().storage_root.resolve()
        storage.mkdir(parents=True, exist_ok=True)
        with tarfile.open(target / "storage.tar.gz", "w:gz") as archive:
            archive.add(storage, arcname="storage")
        effective = {key: normalized(getattr(settings(), key)) for key in (
            "database_url", "cursor_secret", "platform_key_id", "platform_keys", "chroma_host", "chroma_port", "chroma_collection",
            "storage_root", "allowed_origins", "secure_cookies", "stdio_allowed_commands", "stdio_allowed_directories")}
        (target / "effective-config.json").write_text(json.dumps(effective, ensure_ascii=False, indent=2), encoding="utf-8")
        for source, name in [(LOCAL / "infra.env", "infra.env"), (LOCAL / "backend.env", "backend.env"),
                             (LOCAL / "admin-bootstrap.json", "admin-bootstrap.json"), (ROOT / "infra/compose.yaml", "compose.yaml")]:
            if source.exists():
                shutil.copy2(source, target / name)
        # The stopped service cannot provide a consistent logical snapshot; start
        # it only after the physical archive exists. All application writers remain stopped.
        compose("start", "chroma", capture_output=True)
        wait_chroma(settings().chroma_host, settings().chroma_port)
        with db.engine.connect() as connection:
            migration = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        manifest = {"version": "0.0.4", "snapshot_format": "mysql-reflected-v1", "migration": migration, "source_database": source_database, "backup_complete": True,
                    "restore_verified": False, "mysql": relational_snapshot(db.engine),
                    "chroma": chroma_snapshot(vectors.client()), "storage": storage_manifest(storage)}
        manifest["artifacts"] = {p.name: file_hash(p) for p in target.iterdir() if p.is_file()}
        for path in target.iterdir():
            protect(path)
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"backup_complete": True, "restore_verified": False, "directory": str(target)}))
        return target


def wait_chroma(host, port):
    import httpx
    deadline = time.monotonic() + 60
    while True:
        try:
            httpx.get(f"http://{host}:{port}/api/v2/heartbeat", timeout=2).raise_for_status()
            return
        except httpx.HTTPError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Chroma did not become ready") from None
            time.sleep(.5)


def safe_extract(archive, destination):
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk() or not (destination / member.name).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Unsafe backup archive member")
        tar.extractall(destination, filter="data")


def restore_verify(target):
    target = target.resolve()
    if not target.is_relative_to((LOCAL / "backups").resolve()):
        raise RuntimeError("Only local v004 backup directories are accepted")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("backup_complete") or manifest["version"] != "0.0.4" or manifest["migration"] not in {"mysql_0001", "mysql_0002"}:
        raise RuntimeError("Incomplete or incompatible backup")
    for name, expected in manifest["artifacts"].items():
        path = (target / name).resolve()
        if not path.is_relative_to(target) or file_hash(path) != expected:
            raise RuntimeError("Backup component checksum mismatch")
    project = "study-trail-v004-verify-" + uuid4().hex[:12]
    work = LOCAL / "restore-verify" / project
    work.mkdir(parents=True)
    config = yaml.safe_load((target / "compose.yaml").read_text(encoding="utf-8"))
    config["name"] = project
    password = secrets.token_hex(24)
    config["services"]["mysql"]["environment"] = {"MYSQL_ROOT_PASSWORD": password, "MYSQL_DATABASE": "study_trail",
        "MYSQL_USER": "study_trail", "MYSQL_PASSWORD": secrets.token_hex(24)}
    config["services"]["mysql"]["ports"] = ["127.0.0.1:0:3306"]
    config["services"]["chroma"]["ports"] = ["127.0.0.1:0:8000"]
    compose_file = work / "compose.yaml"
    compose_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    protect(compose_file)
    command = [DOCKER, "compose", "-p", project, "-f", str(compose_file)]

    def run(*args, **kwargs):
        return subprocess.run([*command, *args], check=True, **kwargs)

    try:
        run("up", "-d", "--wait", "mysql", capture_output=True)
        run("create", "chroma", capture_output=True)
        container = run("ps", "-a", "-q", "chroma", capture_output=True, text=True).stdout.strip()
        # Validate member names before passing the archive to docker cp.
        with tarfile.open(target / "chroma-data.tar") as tar:
            if any(m.issym() or m.islnk() or Path(m.name).is_absolute() or ".." in Path(m.name).parts for m in tar):
                raise RuntimeError("Unsafe Chroma backup archive")
        with (target / "chroma-data.tar").open("rb") as stream:
            subprocess.run([DOCKER, "cp", "-a", "-", container + ":/data"], stdin=stream, check=True, capture_output=True)
        run("start", "chroma", capture_output=True)
        with (target / "mysql.sql").open("rb") as stream:
            run("exec", "-T", "mysql", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot study_trail', stdin=stream, capture_output=True)
        mysql_port = int(run("port", "mysql", "3306", capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1])
        chroma_port = int(run("port", "chroma", "8000", capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1])
        wait_chroma("127.0.0.1", chroma_port)
        engine = sa.create_engine(sa.URL.create("mysql+pymysql", username="root", password=password, host="127.0.0.1", port=mysql_port, database="study_trail", query={"charset": "utf8mb4"}))
        client = vectors._client("127.0.0.1", chroma_port)
        safe_extract(target / "storage.tar.gz", work)
        if relational_snapshot(engine, legacy_types="snapshot_format" not in manifest) != manifest["mysql"] or chroma_snapshot(client) != manifest["chroma"] or storage_manifest(work / "storage") != manifest["storage"]:
            raise RuntimeError("Restored database, vectors or private files differ")
        effective = json.loads((target / "effective-config.json").read_text(encoding="utf-8"))
        decrypted = 0
        with engine.connect() as conn:
            for row in conn.execute(sa.select(db.credential_versions)).mappings():
                Fernet(effective["platform_keys"][row["key_id"]].encode()).decrypt(row["ciphertext"])
                decrypted += 1
        # Exercise restored trigger definers, not just read their DDL. A missing
        # application account would make restored triggers fail on first use.
        with engine.connect() as conn:
            transaction = conn.begin()
            probe = uuid4()
            conn.execute(db.users.insert().values(id=probe, login="restore_probe", login_normalized="restore_probe",
                display_name="恢复写入探测", password_hash="synthetic-non-login-hash"))
            conn.execute(db.sources.insert().values(id=uuid4(), owner_id=probe, kind="web",
                title_snapshot="恢复触发器探测", url_snapshot="https://example.invalid/restore", excerpt_snapshot="synthetic"))
            transaction.rollback()
        verify_env = {**os.environ, "DATABASE_URL": engine.url.render_as_string(hide_password=False),
                      "CHROMA_HOST": "127.0.0.1", "CHROMA_PORT": str(chroma_port), "CHROMA_COLLECTION": effective["chroma_collection"],
                      "PLATFORM_KEYS": json.dumps(effective["platform_keys"]), "PLATFORM_KEY_ID": effective["platform_key_id"]}
        verification = subprocess.run([sys.executable, "tools/project_python.py", "tools/vector_index.py", "check"], cwd=ROOT / "backend", env=verify_env, capture_output=True)
        if verification.returncode:
            failure_log = work / "vector-verification.error.log"
            failure_log.write_bytes(verification.stderr)
            protect(failure_log)
            raise RuntimeError("Restored vector verification failed; see the private recovery error log")
        queried = 0
        for item in client.list_collections():
            collection = client.get_collection(item.name, embedding_function=None)
            sample = collection.get(limit=1, include=["embeddings"])
            if sample["ids"]:
                result = collection.query(query_embeddings=[sample["embeddings"][0]], n_results=1)
                if not result["ids"][0]:
                    raise RuntimeError("Restored collection query failed")
                queried += 1
        engine.dispose()
        manifest.update(restore_verified=True, decrypted_credentials=decrypted, queried_collections=queried,
                        verified_at=datetime.now(UTC).isoformat(), verification_project=project)
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"backup_complete": True, "restore_verified": True, "decrypted_credentials": decrypted, "queried_collections": queried}))
    finally:
        # No volumes are deleted. Keep this isolated project and recovery artifacts
        # for inspection; stop its containers without touching the live project.
        run("stop", capture_output=True)


def resume():
    with operator_lock():
        compose("up", "-d", "--wait", capture_output=True)
        wait_chroma(settings().chroma_host, settings().chroma_port)
        subprocess.run([sys.executable, "tools/check_installation.py"], cwd=ROOT / "backend", check=True)
        from tools.vector_index import check
        check()
        state = LOCAL / "resume-state.json"
        prior = json.loads(state.read_text(encoding="utf-8")) if state.exists() else []
        (LOCAL / "maintenance").unlink(missing_ok=True)
        if prior:
            if os.name == "nt":
                subprocess.run(["powershell.exe", "-NoProfile", "-File", str(ROOT / "scripts/start.ps1")], cwd=ROOT, check=True)
            else:
                subprocess.run(["bash", str(ROOT / "scripts/start.sh")], cwd=ROOT, check=True)
        state.unlink(missing_ok=True)
        print("Maintenance verification passed; prior application services resumed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backup", "restore-verify", "resume"])
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--allow-test-source", action="store_true", help="Explicitly rehearse an allowlisted isolated test database")
    args = parser.parse_args()
    if args.command == "backup":
        if not 1 <= args.timeout <= 3600:
            parser.error("timeout must be between 1 and 3600 seconds")
        backup(args.timeout, args.allow_test_source)
    elif args.command == "restore-verify":
        if not args.backup:
            parser.error("--backup is required")
        with operator_lock():
            restore_verify(args.backup)
    else:
        resume()


if __name__ == "__main__":
    main()
