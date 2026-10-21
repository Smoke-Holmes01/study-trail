"""Frozen initial 19-table schema with pgvector and ownership guards."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))
    op.get_bind().exec_driver_sql(Path(__file__).with_name("ownership.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Initial migration downgrade is destructive; restore a verified backup instead.")
