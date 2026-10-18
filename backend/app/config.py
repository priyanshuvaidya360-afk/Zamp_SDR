"""Runtime settings, read from environment variables and backend/.env."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Everything below runs on free tiers with no credit card (docs/GUIDE.md, zero-cost rule).
    groq_api_key: str = ""
    gemini_api_key: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""  # optional last-resort search

    # LLM providers in fallback order, all reached through the OpenAI-compatible API.
    llm_providers: list[str] = ["groq", "gemini"]
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # "smart" = hook choice and drafting; "fast" = extraction and classification.
    groq_smart_model: str = "openai/gpt-oss-120b"
    groq_fast_model: str = "openai/gpt-oss-20b"
    gemini_smart_model: str = "gemini-3.6-flash"
    gemini_fast_model: str = "gemini-3.5-flash-lite"

    llm_timeout_s: float = 60
    # Search: Tavily -> DuckDuckGo (ddgs, no key) -> Exa (only if EXA_API_KEY is set).
    ddgs_backends: str = "duckduckgo,bing"  # the ddgs engines that proved reliable
    fetch_timeout_s: float = 10
    fetch_max_chars: int = 20_000
    cache_ttl_hours: float = 24

    database_url: str = f"sqlite:///{BACKEND_DIR / 'signal.db'}"
    # live: real calls. record: real calls + save each response as a fixture.
    # fixtures: replay saved responses only (no network, no keys needed).
    demo_mode: Literal["live", "record", "fixtures"] = "live"
    fixtures_dir: Path = BACKEND_DIR / "tests" / "fixtures"
    cache_dir: Path = BACKEND_DIR / "tests" / "fixtures" / "cache"
    cors_origins: list[str] = ["http://localhost:3000"]
    seller_profile_path: Path = REPO_DIR / "seller_profile.yaml"


settings = Settings()
