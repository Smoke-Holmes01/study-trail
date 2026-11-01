"""Shared deployment identity; never infer a Compose project from ambient variables."""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
LOCAL = ROOT / ".local/v004"
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def compose(*args, **kwargs):
    return subprocess.run([DOCKER, "compose", "-p", "study-trail-v004", "-f", str(ROOT / "infra/compose.yaml"),
                           "--env-file", str(LOCAL / "infra.env"), *args], check=True, **kwargs)


def protect(path):
    if os.name == "nt":
        account = subprocess.check_output(["whoami"], text=True).strip()
        rights = ":(OI)(CI)F" if path.is_dir() else ":F"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", account + rights,
                        "*S-1-5-18" + rights, "*S-1-5-32-544" + rights], check=True, capture_output=True)
    else:
        path.chmod(0o600)
