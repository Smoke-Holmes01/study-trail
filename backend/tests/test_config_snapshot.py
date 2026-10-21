import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID
from study_trail import core, db
from .test_api import post


def test_acceptance_has_one_coherent_agent_configuration(client, resource, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original = core.agent_config

    def held(conn, agent):
        started.set()
        assert release.wait(8)
        return original(conn, agent)

    monkeypatch.setattr(core, "agent_config", held)
    path = f"/conversations/{resource['conversation']['id']}/messages"
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(post, client, path, {"content_text": "问题"})
        assert started.wait(8)
        changing = pool.submit(
            client.patch,
            "/api/v1/agents/" + resource["agent"]["id"],
            json={"system_prompt": "新提示词", "model_id": "gemini-3.8-flash-high"},
        )
        release.set()
        accepted = sending.result(timeout=8).json()["data"]
        assert changing.result(timeout=8).status_code == 200
    with db.engine.begin() as conn:
        task = db.one(conn, db.tasks, UUID(accepted["task"]["id"]))
        assert task["config_snapshot"]["system_prompt"] == resource["agent"]["system_prompt"]
        assert task["config_snapshot"]["model_id"] == resource["agent"]["model_id"]
        assert task["config_snapshot"]["config_version"] == 1
