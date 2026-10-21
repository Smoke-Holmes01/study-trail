"""Require a verified index identity on every published document chunk."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Never infer compatibility from dimensions or the current default model.
    conn.exec_driver_sql("""
        UPDATE document_chunks AS c SET index_fingerprint=f.index_fingerprint
        FROM files AS f WHERE c.file_id=f.id AND c.owner_id=f.owner_id
        AND c.index_fingerprint IS NULL AND f.index_fingerprint IS NOT NULL
    """)
    missing = conn.scalar(sa.text("SELECT count(*) FROM document_chunks WHERE index_fingerprint IS NULL"))
    conflicts = conn.scalar(sa.text("""
        SELECT count(*) FROM document_chunks c JOIN files f ON f.id=c.file_id
        WHERE f.index_fingerprint IS NULL OR c.index_fingerprint<>f.index_fingerprint
    """))
    if missing or conflicts:
        raise RuntimeError("Index identity is missing or inconsistent; restore verified file identities before retrying 0006.")
    op.alter_column("document_chunks", "index_fingerprint", existing_type=sa.String(64), nullable=False)


def downgrade():
    op.alter_column("document_chunks", "index_fingerprint", existing_type=sa.String(64), nullable=True)
