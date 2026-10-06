import json
import os
import re
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parents[1] / ".env"
IDENTIFIER = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
ENV_REFERENCE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def config_error():
    from .core import Problem

    return Problem("CONFIG_INVALID", "后台配置不可用，请联系维护者", 503)


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1)
    api_key: str = ""

    @field_validator("api_key")
    @classmethod
    def credential_reference(cls, value):
        if value and not ENV_REFERENCE.fullmatch(value):
            raise ValueError("模型密钥必须通过环境变量引用")
        return value


class ChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    api_model_id: str
    display_name: str
    enabled: bool = True
    supports_images: bool
    input_token_budget: int = Field(gt=0, strict=True)
    max_output_tokens: int = Field(gt=0, strict=True)
    max_images: int = Field(ge=0, strict=True)
    max_image_bytes: int = Field(ge=0, strict=True)
    json_object: bool = False
    verified_at: str | None = None
    budget_basis: str | None = None


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_model_id: str
    providers: dict[str, Provider]
    models: dict[str, ChatModel]


class MCPServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    transport: str
    enabled: bool = True
    default_enabled: bool = False
    allowed_tools: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=30)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers", "env")
    @classmethod
    def credential_references(cls, value):
        for key, item in value.items():
            sensitive = any(
                part in key.lower() for part in ("authorization", "key", "token", "secret", "password")
            )
            if sensitive and item:
                remainder = ENV_REFERENCE.sub("", item).strip()
                if not ENV_REFERENCE.search(item) or remainder not in {"", "Bearer", "Basic", "Token"}:
                    raise ValueError("MCP 凭据必须通过环境变量引用")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")
    database_url: str = "postgresql+psycopg://study_trail:study_trail@127.0.0.1:54329/study_trail"
    generation_base_url: str = "http://23.94.163.17:8045/v1"
    generation_api_key: str = ""
    siliconflow_api_key: str = ""
    default_model_id: str = "gemini-3.7-flash-high"
    storage_root: Path = Path(__file__).parents[2] / ".local/storage"
    config_root: Path = Path(__file__).parents[3] / "config"
    cursor_secret: str = ""
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    secure_cookies: bool = False
    model_capabilities_file: Path = Path(__file__).parents[2] / ".local/model-capabilities.json"
    exa_api_key: str = ""
    exa_url: str = "https://mcp.exa.ai/mcp?tools=web_search_exa"
    ai_concurrency: int = 2
    file_concurrency: int = 1

    def read_config(self, name):
        try:
            value = json.loads((self.config_root / name).read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError):
            raise config_error() from None

    def model_catalog(self):
        try:
            catalog = ModelCatalog.model_validate(self.read_config("model.json"))
            if catalog.default_model_id not in catalog.models:
                raise ValueError()
            if any(m.provider_id not in catalog.providers for m in catalog.models.values()):
                raise ValueError()
            return catalog
        except ValueError:
            raise config_error() from None

    def models(self):
        catalog = self.model_catalog()
        return {
            ident: {**model.model_dump(), "provider": catalog.providers[model.provider_id].model_dump()}
            for ident, model in catalog.models.items()
            if model.enabled
        }

    def default_model(self):
        return self.model_catalog().default_model_id

    def mcp_servers(self):
        try:
            raw = self.read_config("mcp_config.json")
            if set(raw) != {"mcpServers"} or not isinstance(raw["mcpServers"], dict):
                raise ValueError()
            servers = {}
            for ident, value in raw["mcpServers"].items():
                server = MCPServer.model_validate(value)
                if not re.fullmatch(IDENTIFIER, ident) or len(ident) > 64:
                    raise ValueError()
                if server.transport == "stdio":
                    if not server.command or server.url:
                        raise ValueError()
                elif server.transport == "streamable_http":
                    if not server.url or server.command:
                        raise ValueError()
                else:
                    raise ValueError()
                servers[ident] = server.model_dump()
            return servers
        except ValueError:
            raise config_error() from None

    def resolve(self, value):
        """Resolve credentials at use time; never put resolved values in task snapshots."""
        environment = {**dotenv_values(ENV_FILE), **os.environ}

        def replace(match):
            name = match[1]
            # Resolve fresh .env values, including removal; cached Settings must
            # not resurrect a credential that the deployer has removed.
            fallback = (
                Settings.model_fields["generation_base_url"].default if name == "GENERATION_BASE_URL" else ""
            )
            return str(environment.get(name, fallback) or "")

        return ENV_REFERENCE.sub(replace, value)


@lru_cache
def settings():
    return Settings()
