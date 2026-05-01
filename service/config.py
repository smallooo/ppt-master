"""Service runtime settings, sourced from environment variables / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVICE_ROOT = REPO_ROOT / ".service-data"


class ServiceSettings(BaseSettings):
    """All runtime knobs for the FastAPI service.

    Values are read from process env first, then the project-root ``.env`` file.
    """

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- HTTP server ----------
    host: str = Field(default="127.0.0.1", validation_alias="PPT_SERVICE_HOST")
    port: int = Field(default=8000, validation_alias="PPT_SERVICE_PORT")
    reload: bool = Field(default=False, validation_alias="PPT_SERVICE_RELOAD")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # ---------- Storage ----------
    workspace_root: Path = Field(
        default=DEFAULT_SERVICE_ROOT,
        validation_alias="PPT_SERVICE_WORKSPACE_ROOT",
    )
    max_upload_size_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias="PPT_SERVICE_MAX_UPLOAD_SIZE",
    )
    allowed_source_extensions: str = Field(
        default=".md,.markdown,.txt,.pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.csv,.epub,.html,.htm",
        validation_alias="PPT_SERVICE_ALLOWED_SOURCE_EXTENSIONS",
    )

    # ---------- Database ----------
    database_url: str = Field(
        default="postgresql+psycopg://ppt:ppt_dev_pwd@127.0.0.1:5432/ppt_master",
        validation_alias="DATABASE_URL",
    )

    # ---------- WeChat mini-program ----------
    wechat_appid: str = Field(default="", validation_alias="WECHAT_APPID")
    wechat_appsecret: str = Field(default="", validation_alias="WECHAT_APPSECRET")
    session_secret: str = Field(
        default="dev-only-do-not-use-in-prod",
        validation_alias="SESSION_SECRET",
    )
    session_ttl_seconds: int = Field(
        default=30 * 24 * 3600,
        validation_alias="SESSION_TTL_SECONDS",
    )
    admin_token: str = Field(default="", validation_alias="ADMIN_TOKEN")
    cors_allow_origins: str = Field(
        default="*",
        validation_alias="PPT_SERVICE_CORS_ALLOW_ORIGINS",
    )

    # ---------- OpenAI ----------
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_BASE_URL",
    )
    openai_model_strategist: str = Field(
        default="gpt-4o", validation_alias="OPENAI_MODEL_STRATEGIST"
    )
    openai_model_executor: str = Field(
        default="gpt-4o", validation_alias="OPENAI_MODEL_EXECUTOR"
    )
    openai_model_image: str = Field(
        default="gpt-image-1", validation_alias="OPENAI_MODEL_IMAGE"
    )
    llm_max_tokens_per_job: int = Field(
        default=400_000, validation_alias="LLM_MAX_TOKENS_PER_JOB"
    )

    # ---------- Derived ----------
    @property
    def projects_root(self) -> Path:
        return Path(self.workspace_root).expanduser() / "projects"

    @property
    def allowed_extensions_set(self) -> set[str]:
        return {
            ext.strip().lower()
            for ext in (self.allowed_source_extensions or "").split(",")
            if ext.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    return ServiceSettings()
