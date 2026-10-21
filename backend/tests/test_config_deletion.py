from uuid import UUID, uuid4
from pathlib import Path
import importlib.util

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from study_trail import db, platform, skills
from study_trail.bootstrap import initialize, refresh_builtin_guide
from .test_management import admin as admin
from .test_management import configs, change


def remove(admin, kind, row, *, key=None, expected=None):
    return change(admin, f"/configs/{kind}/{row['id']}", {
        "expected_version": row["lock_version"] if expected is None else expected,
    }, key=key, method="DELETE")


@pytest.mark.parametrize("kind", ["model", "mcp", "prompt", "skill"])
def test_delete_hides_all_catalogs_preserves_history_and_is_idempotent(admin, kind):
    original = configs(admin, kind)[0]
    body = {"stable_id": "delete-" + kind, "name": "可删除配置", "payload": original["payload"], "enabled": False}
    if kind == "prompt":
        body["purpose"] = original["purpose"]
    if kind == "model":
        body["secret_updates"] = {"api_key": "private-test-credential"}
    if kind == "skill":
        body["payload"] = {**body["payload"], "default_enabled": False}
    created = change(admin, f"/configs/{kind}", body, method="POST")
    assert created.status_code == 201, created.text
    row = created.json()["data"]
    assert remove(admin, kind, row, expected=row["lock_version"] + 1).status_code == 409
    key = str(uuid4())
    response = remove(admin, kind, row, key=key)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"deleted_id": row["id"]}
    assert remove(admin, kind, row, key=key).json()["data"] == response.json()["data"]
    assert remove(admin, kind, row).status_code == 404
    assert admin.get(f"/api/admin/configs/{kind}/{row['id']}").status_code == 404
    assert row["id"] not in {item["id"] for item in configs(admin, kind)}
    assert row["stable_id"] not in platform.catalog(kind, include_disabled=True)
    assert change(admin, f"/configs/{kind}/{row['id']}/status", {"enabled": True, "expected_version": row["lock_version"] + 1}).status_code == 404
    assert change(admin, f"/configs/{kind}/{row['id']}", {"name": "复活", "expected_version": row["lock_version"] + 1}).status_code == 404
    assert change(admin, f"/configs/{kind}", body, method="POST").status_code == 409
    with db.engine.connect() as conn:
        stored = db.one(conn, db.platform_configs, UUID(row["id"]))
        assert not stored["enabled"] and stored["deleted_at"] is not None
        revision = db.one(conn, db.config_revisions, UUID(row["current_revision_id"]))
        assert revision["payload"] == row["payload"]
        if kind == "model":
            assert platform.decrypt(revision["credential_version_ids"]["api_key"]) == "private-test-credential"
        assert conn.scalar(sa.select(sa.func.count()).select_from(db.admin_operation_logs).where(
            db.admin_operation_logs.c.target_id == UUID(row["id"]),
            db.admin_operation_logs.c.action.like("DELETE %"),
            db.admin_operation_logs.c.result == "success",
        )) == 1


@pytest.mark.parametrize("kind", ["model", "prompt"])
def test_bound_config_must_be_replaced_before_deletion(admin, kind):
    bound_ids = {b["config_id"] for b in admin.get("/api/admin/bindings").json()["data"]["items"]}
    row = next(row for row in configs(admin, kind) if row["id"] in bound_ids)
    result = remove(admin, kind, row)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "CONFIG_IN_USE"
    assert admin.get(f"/api/admin/configs/{kind}/{row['id']}").status_code == 200


@pytest.mark.parametrize("kind,field", [("model", "model_id"), ("mcp", "mcp_server_ids"), ("skill", "skill_ids")])
def test_agent_selection_blocks_delete_until_unlinked(admin, client, resource, kind, field):
    rows = configs(admin, kind)
    row = next(r for r in rows if r["stable_id"] == "gemini-3.7-flash-high") if kind == "model" else rows[0]
    agent_id = resource["agent"]["id"]
    selected = row["stable_id"] if kind == "model" else [row["stable_id"]]
    patch = client.patch("/api/v1/agents/" + agent_id, json={field: selected})
    assert patch.status_code == 200, patch.text
    blocked = remove(admin, kind, row)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CONFIG_IN_USE"
    if kind == "model":
        accepted = client.post(f"/api/v1/conversations/{resource['conversation']['id']}/messages", json={"content_text": "解释向量"}, headers={"Idempotency-Key": str(uuid4())})
        assert accepted.status_code == 202, accepted.text
        task_id = UUID(accepted.json()["data"]["task"]["id"])
        with db.engine.connect() as conn:
            frozen = dict(db.one(conn, db.tasks, task_id)["config_snapshot"])
    replacement = "gemini-3.8-flash-high" if kind == "model" else []
    assert client.patch("/api/v1/agents/" + agent_id, json={field: replacement}).status_code == 200
    assert remove(admin, kind, row).status_code == 200
    if kind == "model":
        from study_trail.providers import Gateway
        with db.engine.connect() as conn:
            assert db.one(conn, db.tasks, task_id)["config_snapshot"] == frozen
        assert Gateway({"config_snapshot": frozen}).connection()[1]["Authorization"] == "Bearer test-secret"


def test_deleted_builtin_is_not_recreated_and_admin_permissions_apply(admin, client, student, monkeypatch):
    row = next(r for r in configs(admin, "skill") if r["stable_id"] == "study-trail-guide")
    path = f"/api/admin/configs/skill/{row['id']}"
    body = {"expected_version": row["lock_version"]}
    assert client.request("DELETE", path, json=body, headers={"Idempotency-Key": str(uuid4())}).status_code == 401
    assert admin.request("DELETE", path, json=body, headers={"X-CSRF-Token": "", "Idempotency-Key": str(uuid4())}).status_code == 403
    assert remove(admin, "skill", row).status_code == 200
    with db.engine.begin() as conn:
        initialize(conn)
    assert "study-trail-guide" not in platform.catalog("skill", include_disabled=True)
    source = skills.file_catalog()
    source["study-trail-guide"] = {**source["study-trail-guide"], "body": "升级后的指南正文", "version": "new-release"}
    monkeypatch.setattr(skills, "file_catalog", lambda: source)
    actor = UUID(admin.get("/api/admin/me").json()["data"]["id"])
    with db.engine.begin() as conn:
        refresh_builtin_guide(conn, actor)
        initialize(conn)
        assert db.one(conn, db.platform_configs, UUID(row["id"]))["deleted_at"] is not None
    assert "study-trail-guide" not in platform.catalog("skill", include_disabled=True)


def test_0007_upgrade_preserves_existing_rows_and_downgrade_refuses_resurrection():
    path = Path(__file__).parents[1] / "alembic/versions/0007_config_deletion.py"
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TEMP TABLE platform_configs (id int PRIMARY KEY, enabled boolean NOT NULL) ON COMMIT DROP")
        conn.exec_driver_sql("INSERT INTO platform_configs VALUES (1, true)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert conn.exec_driver_sql("SELECT enabled, deleted_at FROM platform_configs WHERE id=1").one() == (True, None)
            with conn.begin_nested():
                with pytest.raises(sa.exc.IntegrityError):
                    with conn.begin_nested():
                        conn.exec_driver_sql("UPDATE platform_configs SET deleted_at=now() WHERE id=1")
            conn.exec_driver_sql("UPDATE platform_configs SET enabled=false, deleted_at=now() WHERE id=1")
            with pytest.raises(RuntimeError, match="Deleted configurations exist"):
                migration.downgrade()
