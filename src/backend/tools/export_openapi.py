import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from study_trail.api import app

target = Path(__file__).parents[2] / ".local/openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
print("Exported public OpenAPI, endpoints:", len(app.openapi()["paths"]))
