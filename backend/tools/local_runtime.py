"""Shared deployment identity; never infer a Compose project from ambient variables."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
from study_trail.config import settings

LOCAL = settings().runtime_root.resolve()
PROJECT = os.environ.get("STUDY_TRAIL_COMPOSE_PROJECT", "study-trail-v004")
if (LOCAL == (ROOT / ".local/v004").resolve()) != (PROJECT == "study-trail-v004"):
    raise RuntimeError("Runtime and Compose project identities must both select daily or both select isolation")
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def compose(*args, **kwargs):
    return subprocess.run([DOCKER, "compose", "-p", PROJECT, "-f", str(ROOT / "infra/compose.yaml"),
                           "--env-file", str(LOCAL / "infra.env"), *args], check=True, **kwargs)


def protect(path):
    if os.name == "nt":
        account = subprocess.check_output(["whoami"], text=True).strip()
        rights = ":(OI)(CI)F" if path.is_dir() else ":F"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", account + rights,
                        "*S-1-5-18" + rights, "*S-1-5-32-544" + rights], check=True, capture_output=True)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)
