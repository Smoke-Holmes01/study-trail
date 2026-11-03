"""Operate only processes whose executable and creation time match our registry."""
import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import psutil

from tools.local_runtime import LOCAL


def entries():
    path = LOCAL / "processes.json"
    if not path.exists():
        return []
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    return result if isinstance(result, list) else [result]


def matching(entry):
    try:
        process = psutil.Process(entry["id"])
        expected = entry.get("started_at")
        expected = datetime.fromisoformat(expected).timestamp() if isinstance(expected, str) else expected
        if os.path.normcase(os.path.realpath(process.exe())) != os.path.normcase(os.path.realpath(entry["path"])) or expected is None or abs(process.create_time() - expected) > .02:
            raise RuntimeError("Recorded process identity changed; refusing to operate on reused PID")
        return process
    except psutil.NoSuchProcess:
        return None


def running():
    return [entry for entry in entries() if matching(entry)]


def writer_processes():
    """Discover project-owned module processes, including orphaned redirector children."""
    backend = Path(__file__).parents[1].resolve()
    result = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = process.info["cmdline"] or []
            if any(item in {"study_trail.api:app", "study_trail.execution", "tools.e2e_app:app", "tools/e2e_app.py"} for item in command) and Path(process.cwd()).resolve() == backend:
                environment = process.environ()
                runtime = environment.get("RUNTIME_ROOT")
                env_file = environment.get("STUDY_TRAIL_ENV_FILE")
                identity = Path(runtime).resolve() if runtime else (Path(env_file).resolve().parent if env_file else (backend.parent / ".local/v004").resolve())
                if identity == LOCAL.resolve():
                    result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def stop(roles=None):
    retained = []
    for entry in entries():
        if roles and entry["name"] not in roles:
            retained.append(entry)
            continue
        process = matching(entry)
        if process:
            descendants = process.children(recursive=True)
            for child in reversed(descendants):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            process.terminate()
            _, alive = psutil.wait_procs([*descendants, process], timeout=10)
            for child in alive:
                child.kill()
            _, alive = psutil.wait_procs(alive, timeout=5)
            if alive:
                raise RuntimeError("Managed processes did not stop")
    (LOCAL / "processes.json").write_text(json.dumps(retained), encoding="utf-8")


def record(name, pid):
    process = psutil.Process(pid)
    result = [e for e in entries() if e["name"] != name]
    result.append({"id": pid, "name": name, "path": process.exe(), "started_at": datetime.fromtimestamp(process.create_time(), UTC).isoformat()})
    (LOCAL / "processes.json").write_text(json.dumps(result), encoding="utf-8")


def stop_e2e():
    registry = LOCAL / "e2e-processes.json"
    if not registry.exists():
        return
    decoded = json.loads(registry.read_text(encoding="utf-8-sig"))
    for entry in decoded if isinstance(decoded, list) else [decoded]:
        process = matching(entry)
        if process:
            children = process.children(recursive=True)
            for candidate in [*reversed(children), process]:
                try:
                    candidate.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs([*children, process], timeout=10)
            if alive:
                raise RuntimeError("E2E processes did not stop; retaining registry")
    registry.write_text("[]", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stop", "stop-e2e", "record", "status"])
    parser.add_argument("--role", choices=["api", "worker", "frontend"])
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    if args.command == "stop-e2e":
        stop_e2e()
    elif args.command == "stop":
        stop([args.role] if args.role else None)
    elif args.command == "record":
        if not args.role or not args.pid:
            parser.error("record requires --role and --pid")
        record(args.role, args.pid)
    else:
        print(json.dumps(running()))
