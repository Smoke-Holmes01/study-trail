import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotenv import dotenv_values

backend = Path(__file__).parents[1]
env = dotenv_values(backend / ".env")
url = sa.engine.make_url(env["DATABASE_URL"])
os.environ["DATABASE_URL"] = url.set(database="study_trail_test").render_as_string(hide_password=False)
os.environ["PLATFORM_KEYS"] = '{"test-v1":"dGVzdC1vbmx5LWtleS0zMi1ieXRlcy1sb25nISEhISE="}'
os.environ["PLATFORM_KEY_ID"] = "test-v1"
os.environ["STORAGE_ROOT"] = str(backend.parent / ".local/test-storage")

from fastapi.testclient import TestClient

from study_trail import db
from study_trail.api import app
from study_trail.config import settings


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    assert db.engine.url.database == "study_trail_test"
    with db.engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE users, platform_configs, config_revisions, credential_versions, platform_bindings, resource_events, task_run_facts, invocation_attempts, admin_operation_logs, metric_coverage, migration_imports CASCADE")
        from study_trail.bootstrap import initialize
        initialize(conn)
        from tools.seed_test_platform import seed_test_platform
        seed_test_platform(conn)
    caps = {
        name: {
            "enabled": True,
            "supports_images": True,
            "json_object": True,
            "input_token_budget": 32768,
            "max_output_tokens": 4096,
            "max_images": 6,
            "max_image_bytes": 62914560,
        }
        for name in ["gemini-3.7-flash-high", "gemini-3.8-flash-high"]
    }
    monkeypatch.setattr(type(settings()), "models", lambda self: caps)
    # No live built-in search connections during isolated regression.
    monkeypatch.setattr("study_trail.builtin_search.configuration", lambda: None)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as client:
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
