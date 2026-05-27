"""Configuration management using Pydantic Settings."""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # AI / LLM
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    llm_model: str = "gpt-4o"

    # Database
    database_url: str = "postgresql://user:pass@host:5432/inventory_db"

    # Veeqo
    veeqo_api_key: Optional[str] = None
    veeqo_base_url: str = "https://api.veeqo.com"

    # Mintsoft
    mintsoft_api_key: Optional[str] = None
    mintsoft_base_url: str = "https://api.mintsoft.co.uk"

    # Slack
    slack_webhook_url: Optional[str] = None
    slack_interactive_url: Optional[str] = None
    slack_channel: str = "#inventory-ops"

    # Agent Config
    sync_interval_minutes: int = 15
    transfer_fee_override: float = 0.0

    @property
    def llm_provider(self) -> str:
        """Determine which LLM provider to use based on available API keys."""
        if self.openai_api_key:
            return "openai"
        elif self.anthropic_api_key:
            return "anthropic"
        raise ValueError("No LLM API key configured (openai_api_key or anthropic_api_key)")


settings = Settings()