"""Deployment catalog selections and message skill labels."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("mcp_server_ids", JSONB, nullable=False, server_default="[]"))
    op.add_column("agents", sa.Column("skill_ids", JSONB, nullable=False, server_default="[]"))
    op.add_column("messages", sa.Column("skill", JSONB, nullable=True))
    op.execute(
        "UPDATE agents SET mcp_server_ids = '[\"exa\"]'::jsonb, "
        'skill_ids = \'["explain","study-plan","practice"]\'::jsonb'
    )
    op.create_check_constraint("agents_mcp_array", "agents", "jsonb_typeof(mcp_server_ids) = 'array'")
    op.create_check_constraint("agents_skills_array", "agents", "jsonb_typeof(skill_ids) = 'array'")


def downgrade():
    op.drop_constraint("agents_skills_array", "agents", type_="check")
    op.drop_constraint("agents_mcp_array", "agents", type_="check")
    op.drop_column("messages", "skill")
    op.drop_column("agents", "skill_ids")
    op.drop_column("agents", "mcp_server_ids")
