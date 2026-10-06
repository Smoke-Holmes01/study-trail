import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).parents[1] / ".env", extra="ignore")
    database_url: str = "postgresql+psycopg://study_trail:study_trail@127.0.0.1:54329/study_trail"
    generation_base_url: str = "http://23.94.163.17:8045/v1"
    generation_api_key: str = ""
    siliconflow_api_key: str = ""
    default_model_id: str = "gemini-3.7-flash-high"
    storage_root: Path = Path(__file__).parents[2] / ".local/storage"
    cursor_secret: str = ""
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    secure_cookies: bool = False
    model_capabilities_file: Path = Path(__file__).parents[2] / ".local/model-capabilities.json"
    exa_api_key: str = ""
    exa_url: str = "https://mcp.exa.ai/mcp?tools=web_search_exa"
    ai_concurrency: int = 2
    file_concurrency: int = 1

    def models(self):
        try:
            catalog = json.loads(self.model_capabilities_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(catalog, dict):
            return {}
        allowed = {
            "enabled",
            "supports_images",
            "input_token_budget",
            "max_output_tokens",
            "max_images",
            "max_image_bytes",
            "json_object",
            "verified_at",
            "budget_basis",
        }
        result = {}
        for ident, raw in catalog.items():
            if not isinstance(ident, str) or not isinstance(raw, dict):
                continue
            value = {key: item for key, item in raw.items() if key in allowed}
            if not isinstance(value.get("supports_images"), bool):
                continue
            if not all(
                type(value.get(key)) is int and value[key] > 0
                for key in ["input_token_budget", "max_output_tokens"]
            ):
                continue
            if not all(
                type(value.get(key)) is int and value[key] >= 0 for key in ["max_images", "max_image_bytes"]
            ):
                continue
            if value.get("enabled") is True:
                result[ident] = value
        return result


@lru_cache
def settings():
    return Settings()
