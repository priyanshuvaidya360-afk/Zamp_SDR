"""Runtime settings, read from environment variables and backend/.env."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    anthropic_api_key: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""

    sonnet_model: str = "claude-sonnet-5-5"
    haiku_model: str = "claude-haiku-5-5"

    database_url: str = f"sqlite:///{BACKEND_DIR / 'signal.db'}"
    demo_mode: Literal["live", "record", "fixtures"] = "live"
    cors_origins: list[str] = ["http://localhost:3000"]
    seller_profile_path: Path = REPO_DIR / "seller_profile.yaml"


settings = Settings()
