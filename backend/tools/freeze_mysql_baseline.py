"""Development-only baseline freezer. The resulting SQL is committed and immutable."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, sort_tables_and_constraints
from study_trail.db import metadata
from study_trail.mysql_guards import statements

target = Path(__file__).parents[1] / "alembic/versions/mysql_0001_schema.json"
if target.exists():
    raise RuntimeError("Frozen baseline already exists; use a new migration")
dialect = mysql.dialect()
sql = []
for table, constraints in sort_tables_and_constraints(metadata.tables.values()):
    if table is None:
        sql.extend(str(AddConstraint(c).compile(dialect=dialect)) for c in sorted(constraints, key=lambda c: c.name))
    else:
        sql.append(str(CreateTable(table, include_foreign_key_constraints=constraints).compile(dialect=dialect)))
        sql.extend(str(CreateIndex(i).compile(dialect=dialect)) for i in sorted(table.indexes, key=lambda i: i.name))
sql.extend(statements())
sql.append("INSERT INTO runtime_locks(scope,bucket) VALUES " + ",".join(["('platform',0)", "('capacity',0)", *[f"('idem',{n})" for n in range(256)]]))
target.write_text(json.dumps(sql, ensure_ascii=False, indent=2), encoding="utf-8")
print("Frozen MySQL baseline:", len(metadata.tables), "tables")
