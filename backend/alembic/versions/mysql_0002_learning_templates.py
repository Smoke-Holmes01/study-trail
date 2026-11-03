"""Preserving v0.1.0 upgrade: roles, independent learning content and templates."""
import sqlalchemy as sa
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from alembic import op

revision = "mysql_0002"
down_revision = "mysql_0001"
branch_labels = None
depends_on = None

NEW_TABLES = ("task_role_runs", "agent_templates", "template_versions", "template_assets",
    "template_version_assets", "template_adoptions", "template_import_batches", "template_import_items",
    "template_source_mappings", "exercise_revisions", "exercise_plan_links", "exercise_sources", "learning_plan_revisions")

def fk_key(fk):
    return (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]), fk.get("options", {}).get("ondelete", "NO ACTION"))

def target_key(fk):
    return (tuple(c.name for c in fk.columns), fk.referred_table.name,
        tuple(e.column.name for e in fk.elements), fk.ondelete or "NO ACTION")

def upgrade():
    from study_trail import db
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        raise RuntimeError("mysql_0002 requires MySQL")
    existing = set(sa.inspect(conn).get_table_names()) - {"alembic_version"}
    if len(existing) != 31 or existing.intersection(NEW_TABLES):
        raise RuntimeError("Expected complete mysql_0001; restore verified backup after interrupted DDL")
    # Remove old guards before changing their referenced columns. Reinstall below.
    triggers = conn.exec_driver_sql("SHOW TRIGGERS").mappings().all()
    for trigger in triggers:
        if trigger["Trigger"].startswith("guard_"):
            conn.exec_driver_sql("DROP TRIGGER `" + trigger["Trigger"] + "`")
    inspector = sa.inspect(conn)
    for name in existing:
        target = db.metadata.tables[name]
        desired = {target_key(x) for x in target.foreign_key_constraints}
        for fk in inspector.get_foreign_keys(name):
            if fk_key(fk) not in desired:
                op.drop_constraint(fk["name"], name, type_="foreignkey")
        for check in inspector.get_check_constraints(name):
            op.drop_constraint(check["name"], name, type_="check")
        columns = {x["name"]: x for x in inspector.get_columns(name)}
        for column in target.c:
            if column.name not in columns:
                op.add_column(name, sa.Column(column.name, column.type, nullable=column.nullable, server_default=column.server_default))
        if name == "exercises":
            for field in ("message_id", "conversation_id"):
                op.alter_column(name, field, existing_type=sa.CHAR(32), nullable=True)
        if name == "sources":
            op.alter_column(name, "kind", existing_type=sa.String(12), type_=sa.String(24), existing_nullable=False)
    for name in NEW_TABLES:
        conn.execute(CreateTable(db.metadata.tables[name], include_foreign_key_constraints=[]))
    # New unique constraints must exist before adding the ownership foreign keys.
    for name in existing:
        inspector = sa.inspect(conn)
        existing_unique = {tuple(x["column_names"]) for x in inspector.get_unique_constraints(name)}
        for constraint in db.metadata.tables[name].constraints:
            if isinstance(constraint, sa.UniqueConstraint) and tuple(c.name for c in constraint.columns) not in existing_unique:
                conn.execute(AddConstraint(constraint))
    # Backfill immutable history before changing message deletion semantics.
    from study_trail.learning_content import record_exercise, record_plan
    for row in conn.execute(sa.select(db.learning_plans)).mappings().all():
        record_plan(conn, row)
    frozen_exercises = {}
    from study_trail.core import freeze_exercise
    for row in conn.execute(sa.select(db.exercises)).mappings().all():
        current = record_exercise(conn, row)
        revision_id, snapshot = freeze_exercise(conn, current)
        frozen_exercises[str(current["id"])] = (revision_id, snapshot, current["owner_id"])
        conn.execute(db.messages.update().where(db.messages.c.answer_exercise_id == current["id"],
            db.messages.c.owner_id == current["owner_id"]).values(exercise_revision_id=revision_id, exercise_snapshot=snapshot))
    for task in conn.execute(sa.select(db.tasks)).mappings().all():
        inp = task["input_snapshot"]
        value = inp.get("answer_exercise_id") or inp.get("feedback_exercise_id")
        if not value:
            continue
        from uuid import UUID
        try:
            frozen = frozen_exercises.get(str(UUID(str(value))))
        except (ValueError, TypeError):
            frozen = None
        if frozen and frozen[2] == task["owner_id"]:
            revision_id, snapshot, _ = frozen
            conn.execute(db.tasks.update().where(db.tasks.c.id == task["id"]).values(
                exercise_revision_id=revision_id, exercise_snapshot=snapshot,
                input_snapshot={**inp, "exercise_snapshot": snapshot}))
    for name, target in db.metadata.tables.items():
        inspector = sa.inspect(conn)
        present = {fk_key(x) for x in inspector.get_foreign_keys(name)}
        for fk in target.foreign_key_constraints:
            if target_key(fk) not in present:
                conn.execute(AddConstraint(fk))
        if name in existing:
            for check in target.constraints:
                if isinstance(check, sa.CheckConstraint):
                    conn.execute(AddConstraint(check))
        indices = {x["name"] for x in inspector.get_indexes(name)}
        for index in target.indexes:
            if index.name not in indices:
                conn.execute(CreateIndex(index))
    from study_trail.mysql_guards import statements
    remaining = {r["Trigger"] for r in conn.exec_driver_sql("SHOW TRIGGERS").mappings()}
    for statement in statements():
        name = statement.split()[2]
        if name not in remaining:
            conn.exec_driver_sql(statement)
    # Explicit bootstrap adds only missing roles after the current skill catalog exists.

def downgrade():
    raise RuntimeError("v0.1.0 data cannot be destructively downgraded; restore a verified coordinated backup")
