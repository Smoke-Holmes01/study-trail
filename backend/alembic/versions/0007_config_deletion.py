"""Remove administrator configurations from active catalogs while preserving history."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_configs", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_platform_configs_deleted_disabled", "platform_configs", "deleted_at IS NULL OR NOT enabled")


def downgrade():
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT count(*) FROM platform_configs WHERE deleted_at IS NOT NULL")):
        raise RuntimeError("Deleted configurations exist; restore a matching backup rather than resurrecting them.")
    op.drop_constraint("ck_platform_configs_deleted_disabled", "platform_configs", type_="check")
    op.drop_column("platform_configs", "deleted_at")
