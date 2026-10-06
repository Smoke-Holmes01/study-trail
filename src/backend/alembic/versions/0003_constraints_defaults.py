"""Enforce named business checks and usable database defaults."""
from pathlib import Path
from alembic import op
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None
def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_name('hardening.sql').read_text(encoding='utf-8'))
def downgrade():
    raise RuntimeError('Restore a verified backup to revert initial business constraints.')
