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
    llm_max_rate_limit_wait_s: float = 20  # honour a 429 retry-after up to this long...
    llm_rate_limit_waits: int = 2  # ...this many times, before falling back to the next provider
    # Search: Tavily -> DuckDuckGo (ddgs, no key) -> Exa (only if EXA_API_KEY is set).
    ddgs_backends: str = "duckduckgo,bing"  # the ddgs engines that proved reliable
    fetch_timeout_s: float = 10
    fetch_max_chars: int = 20_000
    cache_ttl_hours: float = 24

    # Batch runner (T05). One global cap across all batches keeps us inside free-tier limits.
    max_concurrent_runs: int = 5
    stub_stage_delay_s: float = 0.3  # stand-in stages until T06-T09 replace them
    sse_heartbeat_s: float = 15
    # Pipeline (T06). Spec: docs/GUIDE.md stages 1-3.
    recent_run_days: int = 30  # 0 disables the "researched recently" block (handy in dev)
    identity_accept: float = 0.8  # one candidate at or above this, no rival >= plausible
    identity_plausible: float = 0.5
    gather_max_searches: int = 8
    gather_max_extracts: int = 10
    signal_window_days: int = 180
    source_max_chars: int = 6_000  # per page passed on to extraction (free-tier token limits)

    database_url: str = f"sqlite:///{BACKEND_DIR / 'signal.db'}"
    # live: real calls. record: real calls + save each response as a fixture.
    # fixtures: replay saved responses only (no network, no keys needed).
    demo_mode: Literal["live", "record", "fixtures"] = "live"
    fixtures_dir: Path = BACKEND_DIR / "tests" / "fixtures"
    cache_dir: Path = BACKEND_DIR / "tests" / "fixtures" / "cache"
    cors_origins: list[str] = ["http://localhost:3000"]
    seller_profile_path: Path = REPO_DIR / "seller_profile.yaml"


settings = Settings()
