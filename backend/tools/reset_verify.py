"""Explicitly reset synthetic databases on the independent verification service."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).parents[1]))
import sqlalchemy as sa
from dotenv import dotenv_values
from study_trail.config import settings
from tools.local_runtime import LOCAL, PROJECT, ROOT, compose


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    if LOCAL != (ROOT / ".local/v010-verify").resolve() or PROJECT != "study-trail-v010-verify":
        raise RuntimeError("Reset is restricted to the independent verification profile")
    port = int(compose("port", "mysql", "3306", capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1])
    url = sa.engine.make_url(settings().database_url)
    if url.host != "127.0.0.1" or url.port != port:
        raise RuntimeError("Configuration does not identify the verification container")
    bootstrap = LOCAL / "admin-bootstrap.json"
    if bootstrap.exists():
        bootstrap.rename(LOCAL / ("admin-bootstrap-retired-" + uuid4().hex + ".json"))
    infra = dotenv_values(LOCAL / "infra.env")
    engine = sa.create_engine(url.set(username="root", password=infra["MYSQL_ROOT_PASSWORD"], database=None), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for name in ("study_trail", "study_trail_test", "study_trail_e2e"):
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
        conn.exec_driver_sql("CREATE DATABASE study_trail CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
    engine.dispose()
    print("Synthetic verification databases reset; daily service was not addressed.")


if __name__ == "__main__":
    main()
