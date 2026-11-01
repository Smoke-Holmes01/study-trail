"""Independent v0.0.4 MySQL baseline. PostgreSQL histories are archived separately."""
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "mysql_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        raise RuntimeError("This baseline requires a fresh MySQL database")
    existing = set(sa.inspect(conn).get_table_names()) - {"alembic_version"}
    if existing:
        raise RuntimeError("MySQL baseline requires an empty database; never stamp a PostgreSQL installation")
    for statement in json.loads(Path(__file__).with_name("mysql_0001_schema.json").read_text(encoding="utf-8")):
        conn.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError("Restore a verified backup; automatic destructive baseline downgrade is disabled")
