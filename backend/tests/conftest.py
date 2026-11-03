import os
import socket
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotenv import dotenv_values

backend = Path(__file__).parents[1]
env_path = os.environ.get("STUDY_TRAIL_ENV_FILE")
if not env_path:
    raise RuntimeError("Tests require an explicit isolated STUDY_TRAIL_ENV_FILE")
env = dotenv_values(env_path)
runtime = Path(env.get("RUNTIME_ROOT", "")).resolve()
if runtime == (backend.parent / ".local/v004").resolve() or not env.get("RUNTIME_ROOT"):
    raise RuntimeError("Tests must not use the daily runtime")
os.environ.update({k: v for k, v in env.items() if v is not None})
os.environ["ALLOWED_ORIGINS"] = '["http://127.0.0.1:5173"]'
url = sa.engine.make_url(env["DATABASE_URL"])
os.environ["DATABASE_URL"] = url.set(database="study_trail_test").render_as_string(hide_password=False)
os.environ["CHROMA_COLLECTION"] = "study_trail_test_chunks"
os.environ["PLATFORM_KEYS"] = '{"test-v1":"dGVzdC1vbmx5LWtleS0zMi1ieXRlcy1sb25nISEhISE="}'
os.environ["PLATFORM_KEY_ID"] = "test-v1"
os.environ["STORAGE_ROOT"] = str(runtime / "test-storage")

from fastapi.testclient import TestClient

from study_trail import db
from study_trail.api import app
from study_trail.config import settings


def reset_tables(conn, names=None):
    assert conn.engine.url.database == "study_trail_test"
    conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    try:
        for name in names or [n for n in db.metadata.tables if n != "runtime_locks"]:
            conn.exec_driver_sql(f"TRUNCATE TABLE `{name}`")
    finally:
        conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")



@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    original_resolve = socket.getaddrinfo
    def local_resolve(host, *args, **kwargs):
        if host not in {"127.0.0.1", "localhost", "::1", b"127.0.0.1", b"localhost", None}:
            raise RuntimeError("External network is disabled during regression")
        return original_resolve(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
    from study_trail import vectors
    try:
        vectors.client().delete_collection(settings().chroma_collection)
    except __import__("chromadb").errors.NotFoundError:
        pass
    vectors.collection(create=True)
    assert db.engine.url.database == "study_trail_test"
    with db.engine.begin() as conn:
        reset_tables(conn)
        from study_trail.bootstrap import initialize
        initialize(conn)
        from tools.seed_test_platform import seed_test_platform
        seed_test_platform(conn)
    # No live built-in search connections during isolated regression.
    monkeypatch.setattr("study_trail.builtin_search.configuration", lambda: None)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=os.environ.get("TEST_RAISE_SERVER_EXCEPTIONS") == "1") as client:
        client.headers["Origin"] = "http://127.0.0.1:5173"
        yield client


def login(client, name=None):
    name = name or "test_" + uuid4().hex[:12]
    password = "test password 123"
    response = client.post(
        "/api/v1/auth/register", json={"login": name, "password": password, "confirm_password": password}
    )
    assert response.status_code == 201, response.text
    response = client.post("/api/v1/auth/login", json={"login": name, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
    return response.json()["data"]["student"]


@pytest.fixture
def student(client):
    return login(client)


@pytest.fixture
def resource(client, student):
    response = client.post(
        "/api/v1/agents",
        json={
            "name": "数学伙伴",
            "description": "",
            "system_prompt": "逐步解释数学问题。",
            "knowledge_base_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    agent = response.json()["data"]
    response = client.post(f"/api/v1/agents/{agent['id']}/conversations", json={})
    assert response.status_code == 201, response.text
    return {"student": student, "agent": agent, "conversation": response.json()["data"]}
