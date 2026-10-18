"""Application settings loaded from environment variables and the repository `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration shared by the CLI, integration layer, and later services."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_username: str = ""
    falkordb_password: str = ""
    falkordb_graph: str = "schema_repair"
    falkordb_ssl: bool = False
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_max_tool_steps: int = Field(default=8, ge=1, le=20)
    pr_mode: str = Field(default="dry-run", validation_alias="REPAIR_AGENT_PR_MODE")
    github_repository: str = ""
    github_issue_number: str = ""
    github_comment_mode: Literal["dry-run", "live"] = "dry-run"
    github_app_id: str = ""
    github_app_private_key_path: Path | None = None
    github_app_private_key: SecretStr | None = None
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_auto_demo_scenario: str = ""
    repair_agent_public_url: str = "http://localhost:3002"
    api_port: int = 8002
    namespace_prefix: str = "shop_prod."
    warehouse_dir: str = "demo-warehouse"
    repo_root: Path = REPO_ROOT

    @property
    def warehouse_path(self) -> Path:
        """Resolve the selected warehouse fixture relative to the Relink repository."""

        configured = Path(self.warehouse_dir)
        return configured if configured.is_absolute() else self.repo_root / configured


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide, cached application settings."""

    return Settings()
