"""Fixed professional-role configuration, with immutable platform revisions."""
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import Field

from . import admin, db
from . import core as c
from . import platform as p
from . import schemas as s

router = APIRouter(prefix="/api/admin/roles", tags=["roles"])

class RoleEdit(s.Input):
    expected_version: int = Field(gt=0, strict=True)
    body: str = Field(min_length=1, max_length=65536)
    allowed_mcp_ids: list[str]
    allowed_skill_ids: list[str]

def role(conn, ident, lock=False):
    row = c.require(db.one(conn, db.platform_configs, ident, lock=lock))
    if row["kind"] != "role" or row["stable_id"] not in p.ROLE_NAMES:
        raise c.Problem("RESOURCE_NOT_FOUND", "角色不存在", 404)
    return row

@router.get("", operation_id="AD-RA01")
def listing(ctx=admin.D):
    rows = ctx[0].execute(sa.select(db.platform_configs).where(db.platform_configs.c.kind == "role").order_by(db.platform_configs.c.stable_id)).mappings()
    return c.envelope({"items": [p.projection(ctx[0], r) for r in rows], "next_cursor": None})

@router.get("/{id}", operation_id="AD-RA02")
def detail(id: UUID, ctx=admin.D):
    return c.envelope(p.projection(ctx[0], role(ctx[0], id)))

@router.patch("/{id}", operation_id="AD-RA03")
def edit(id: UUID, body: RoleEdit, request: Request, ctx=admin.D):
    conn, user, _ = ctx
    def action():
        p.lock(conn)
        row = role(conn, id, True)
        admin.version(row, body.expected_version)
        payload = body.model_dump(exclude={"expected_version"})
        for key, kind in (("allowed_mcp_ids", "mcp"), ("allowed_skill_ids", "skill")):
            existing = set(conn.scalars(sa.select(db.platform_configs.c.stable_id).where(db.platform_configs.c.kind == kind, db.platform_configs.c.deleted_at.is_(None))))
            if set(payload[key]) - existing:
                raise c.Problem("VALIDATION_ERROR", "角色包含未知能力", 422)
        p.revision(conn, row, payload, None, user["id"])
        return p.projection(conn, db.one(conn, db.platform_configs, id)), 200
    return admin.write(conn, user, request, body.model_dump(), action, "role", id)

def register(app):
    app.include_router(router)
