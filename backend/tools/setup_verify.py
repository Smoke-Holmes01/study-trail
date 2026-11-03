"""Create a self-contained v0.1.0 verification stack without daily credentials."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).parents[2]
LOCAL = ROOT / ".local/v010-verify"
os.environ["STUDY_TRAIL_ENV_FILE"] = str(LOCAL / "backend.env")
os.environ["RUNTIME_ROOT"] = str(LOCAL)
os.environ["STUDY_TRAIL_COMPOSE_PROJECT"] = "study-trail-v010-verify"
sys.path.insert(0, str(ROOT / "backend"))

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from tools.local_runtime import compose, protect


def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    infra = LOCAL / "infra.env"
    if not infra.exists():
        infra.write_text(f"MYSQL_ROOT_PASSWORD={secrets.token_hex(24)}\nMYSQL_PASSWORD={secrets.token_hex(24)}\nMYSQL_PORT=0\nCHROMA_PORT=0\n", encoding="utf-8")
    protect(infra)
    compose("up", "-d", "--wait")
    def port(service, internal):
        return int(compose("port", service, str(internal), capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1])
    mysql_port, chroma_port = port("mysql", 3306), port("chroma", 8000)
    # Docker reallocates ephemeral host ports after stop/start. Reserve the first
    # assigned values in this profile before any coordinated backup restarts it.
    infra_values = dotenv_values(infra)
    if infra_values.get("MYSQL_PORT") == "0" or infra_values.get("CHROMA_PORT") == "0":
        infra_values.update(MYSQL_PORT=str(mysql_port), CHROMA_PORT=str(chroma_port))
        infra.write_text("".join(f"{key}={value}\n" for key, value in infra_values.items()), encoding="utf-8")
        compose("up", "-d", "--wait")
    target = LOCAL / "backend.env"
    old = dotenv_values(target) if target.exists() else {}
    values = {
        "DATABASE_URL": f"mysql+pymysql://study_trail:{dotenv_values(infra)['MYSQL_PASSWORD']}@127.0.0.1:{mysql_port}/study_trail?charset=utf8mb4",
        "RUNTIME_ROOT": str(LOCAL), "STORAGE_ROOT": str(LOCAL / "storage"),
        "STUDY_TRAIL_COMPOSE_PROJECT": "study-trail-v010-verify",
        "CHROMA_HOST": "127.0.0.1", "CHROMA_PORT": str(chroma_port),
        "CHROMA_COLLECTION": "study_trail_v010_verify_chunks",
        "CURSOR_SECRET": old.get("CURSOR_SECRET") or secrets.token_hex(32),
        "PLATFORM_KEY_ID": "verify-v1",
        "PLATFORM_KEYS": old.get("PLATFORM_KEYS") or json.dumps({"verify-v1": Fernet.generate_key().decode()}),
        "ALLOWED_ORIGINS": json.dumps(["http://127.0.0.1:5180"]),
    }
    target.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()), encoding="utf-8")
    protect(target)
    env = {**os.environ, **values, "PYTHONUTF8": "1"}
    for command in (["-m", "alembic", "upgrade", "head"],
                    ["tools/bootstrap_platform.py", "--admin-login", "verify_admin", "--generate-admin", "--refresh-guide"],
                    ["tools/vector_index.py", "init", "--apply"],
                    ["tools/check_installation.py"]):
        subprocess.run([sys.executable, *command], env=env, cwd=ROOT / "backend", check=True)
    print("Independent verification stack ready; credentials remain in the private verification directory.")


if __name__ == "__main__":
    main()
