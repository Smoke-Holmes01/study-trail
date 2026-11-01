import os
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

IDENTIFIER = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

ENV_FILE = Path(os.environ.get("STUDY_TRAIL_ENV_FILE", Path(__file__).parents[2] / ".local/v004/backend.env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")
    database_url: str = "mysql+pymysql://study_trail:CHANGE_ME@127.0.0.1:53306/study_trail?charset=utf8mb4"
    runtime_root: Path = Path(__file__).parents[2] / ".local/v004"
    storage_root: Path = Path(__file__).parents[2] / ".local/v004/storage"
    chroma_host: str = "127.0.0.1"
    chroma_port: int = 58000
    chroma_collection: str = "study_trail_v004_chunks"
    config_root: Path = Path(__file__).parents[2] / "config"
    cursor_secret: str = ""
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    secure_cookies: bool = False
    platform_key_id: str = "local-v1"
    platform_keys: dict[str, str] = {}
    stdio_allowed_commands: list[str] = []
    stdio_allowed_directories: list[str] = []
    maintenance: bool = False
    ai_concurrency: int = 2
    file_concurrency: int = 1

    def models(self):
        from .platform import catalog
        return catalog("model")

    def default_model(self):
        from . import db
        from .platform import bound, model_readiness
        with db.engine.connect() as conn:
            return bound(conn, "default_generation")[0]["stable_id"] if model_readiness(conn)["default_generation"] else None

    def mcp_servers(self):
        from .platform import catalog
        return catalog("mcp", include_disabled=True)

    def resolve(self, value):
        """Resolve credentials at use time; never put resolved values in task snapshots."""
        if value.startswith("credential:"):
            from .platform import decrypt
            return decrypt(value.removeprefix("credential:"))
        if "{env:" in value:
            from .core import Problem
            raise Problem("CONFIG_INVALID", "运行配置不支持环境凭据引用", 503)
        return value


@lru_cache
def settings():
    return Settings()
