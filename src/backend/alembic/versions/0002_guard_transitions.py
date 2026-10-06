"""Do not reject still-pending SET NULL references during parent cascades."""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_name("ownership_v2.sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a verified backup instead of reverting ownership guards.")
