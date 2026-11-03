"""Run with the project's site initialization when using Windows base Python."""
import runpy
import os
import site
import sys
from pathlib import Path

backend = Path(__file__).parents[1]
sys.path.insert(0, str(backend))
site.addsitedir(str(backend / ".venv/Lib/site-packages"))
os.environ["PYTHONPATH"] = os.pathsep.join([str(backend), str(backend / ".venv/Lib/site-packages"), os.environ.get("PYTHONPATH", "")])
sys.argv = sys.argv[1:]
if not sys.argv:
    raise SystemExit("A module or script is required")
if sys.argv[0] == "-m":
    sys.argv = sys.argv[1:]
    runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
else:
    runpy.run_path(str(Path(sys.argv[0]).resolve()), run_name="__main__")
