"""Runtime settings, read from environment variables and backend/.env."""

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Everything below runs on free tiers with no credit card (docs/PROJECT_SPEC.md, zero-cost rule).
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
    # Groq caps replies at 2,048 tokens unless asked, and gpt-oss spends most of that thinking:
    # a long page's extraction ran out mid-answer. Gemini's own default is already large.
    groq_max_output_tokens: int = 8192
    llm_max_rate_limit_wait_s: float = 30  # honour a 429 retry-after up to this long...
    llm_rate_limit_waits: int = 3  # ...this many times, before falling back to the next provider
    # Search: Tavily -> DuckDuckGo (ddgs, no key) -> Exa (only if EXA_API_KEY is set).
    ddgs_backends: str = "duckduckgo,bing"  # the ddgs engines that proved reliable
    fetch_timeout_s: float = 10
    fetch_max_chars: int = 20_000
    cache_ttl_hours: float = 24

    # Batch runner (T05). One global cap across all batches keeps us inside free-tier limits.
    max_concurrent_runs: int = 5
    stub_stage_delay_s: float = 0.3  # stand-in stages until T06-T09 replace them
    sse_heartbeat_s: float = 15
    # Retrying a failed run: at most this many retries; saved research older than this is redone.
    retry_max_attempts: int = 3
    retry_research_max_age_hours: float = 24
    # Pipeline (T06). Spec: docs/PROJECT_SPEC.md stages 1-3.
    recent_run_days: int = 30  # 0 disables the "researched recently" block (handy in dev)
    identity_accept: float = 0.8  # one candidate at or above this, no rival >= plausible
    identity_plausible: float = 0.5
    identity_unverified: float = 0.3  # identity confidence of a company-level draft (person not found)
    # Research breadth per prospect: 14 fixed searches + the planner's. Core and own-site searches
    # use Tavily first (9, about the old 8); the wider ones (customers, reviews, awards, news,
    # the person's writing) and the planner's use DuckDuckGo first, so they cost no credits.
    gather_max_searches: int = 18
    gather_max_extracts: int = 14
    gather_planned_queries: int = 4  # extra searches the planner (agents/stage3_plan_extra_searches.md) writes; 0 = off
    signal_window_days: int = 365  # facts older than this are dropped (a year: small companies post rarely)
    source_max_chars: int = 6_000  # per page passed on to extraction (free-tier token limits)
    today: date | None = None  # pin "today" for tests and demos; None = the real date
    extract_concurrency: int = 3  # extraction LLM calls in flight per run
    ghost_min_locations: int = 3  # same job title in this many locations -> ghost suspect
    ghost_min_days: int = 30  # or posted at least this long ago
    # Hook score (T08): 0.35 R + 0.25 S + 0.25 * 5e^(-d/120) + 0.15 * 5V. Starting guesses, to be
    # tuned on reply-rate data; the UI shows them. Set to be flexible: a good-enough, truthful fact
    # can open an email; the ethics rules (sensitive, opted out, competitor, unsupported facts) are
    # separate and never loosened by these numbers.
    weight_relevance: float = 0.35
    weight_specificity: float = 0.25
    weight_recency: float = 0.25
    weight_verifiability: float = 0.15
    recency_scale_days: float = 120  # a 4-month-old fact keeps ~37% of its recency points (was 60: 14%)
    undated_days: int = 120
    low_signal_below: float = 1.5  # below this no hook: a labelled role-based draft instead
    high_signal_at: float = 3.0  # a hook scoring at least this is a 'High signal' in the lists
    hook_candidates: int = 3
    # Crowded hook (T14, case 5): a funding / launch / acquisition younger than crowded_max_days
    # and covered by crowded_min_coverage+ outlets loses crowd_penalty and may only be context.
    crowd_penalty: float = 1.5
    crowded_max_days: int = 30
    crowded_min_coverage: int = 5
    # Draft + critic (T09).
    draft_max_words: int = 120
    subject_max_words: int = 6
    genericness_fail_at: int = 3  # 0 = only works for this person, 5 = fully generic
    # Review (T15, case 10): approving a draft older than this re-checks its time-sensitive facts.
    revalidate_after_days: int = 3

    # Intake: every row needs the person's own LinkedIn profile (linkedin.com/in/...): a common
    # name at a big company (two Priya Sharmas at HDFC Bank) can't be told apart by domain alone.
    # False restores "company domain or LinkedIn URL" (the recorded demo's fictional people have none).
    require_linkedin: bool = True

    database_url: str = f"sqlite:///{BACKEND_DIR / 'signal.db'}"
    # live: real calls. record: real calls + save each response as a fixture.
    # fixtures: replay saved responses only (no network, no keys needed).
    demo_mode: Literal["live", "record", "fixtures"] = "live"
    # Demo recording only (T17): fictional companies' pages, served instead of the live web.
    sim_web_dir: Path | None = None
    sim_web_later: bool = False
    record_reuse: bool = False  # record mode: reuse successful recordings (fixtures.reusable)
    record_reuse_dirs: list[Path] = []  # serve each page's later_text (case 10's "twelve days later")
    fixture_delay_s: float = 0  # pause per replayed call, so a fixtures-mode batch streams like a live one
    fixtures_dir: Path = BACKEND_DIR / "tests" / "fixtures"
    cache_dir: Path = BACKEND_DIR / "tests" / "fixtures" / "cache"
    cors_origins: list[str] = ["http://localhost:3000"]
    # Per visitor (IP), per hour, so a shared demo link can't drain the free quotas (app/limits.py).
    # 0 turns a limit off. Uploads cost nothing; each prospect costs ~9 search credits + tokens.
    uploads_per_hour: int = 20
    prospects_per_hour: int = 60
    seller_profile_path: Path = REPO_DIR / "seller_profile.yaml"


settings = Settings()
