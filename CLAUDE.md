# Signal (PS-3 Outreach Engine)

An SDR uploads a CSV of prospects (first name, last name, and a company domain or LinkedIn
URL). For each row, a fixed pipeline resolves the person's identity, researches the public
web, picks one cited hook, and writes a short first-touch email (under 120 words) that waits
in a review queue. The UI shows a live run view (every stage streamed over SSE) and a
dashboard. Nothing is ever sent: the end state is approve / edit / reject by a human.

Specs for each stage live in `docs/GUIDE.md`. Cite the section you used. Build tasks
T01-T18 are at the end of the guide; do one task per session.

## Stack
- Backend: Python 3.15, FastAPI, SQLModel (SQLite locally, Postgres deployed), Pydantic,
  pydantic-settings, httpx, tenacity, pandas, scikit-learn, trafilatura.
- Zero cost: everything runs on free tiers with no credit card. Do not add a paid service.
- LLM: `openai` SDK against OpenAI-compatible endpoints. Groq primary (smart model
  `openai/gpt-oss-120b` for hook choice and drafting, fast model `openai/gpt-oss-20b` for
  extraction and classification), Gemini fallback (`gemini-3.6-flash` /
  `gemini-3.5-flash-lite`). Code asks for a role ("smart" / "fast"), never a model ID.
- Search: Tavily (primary), DuckDuckGo via `ddgs` (no key), Exa (optional, last). Never
  scrape LinkedIn.
- Frontend: Next.js 16 (App Router, TypeScript), Tailwind v4, shadcn/ui. Read
  `frontend/AGENTS.md` before writing frontend code: Next 16 differs from older versions.
- Live updates: SSE (`text/event-stream`) + browser `EventSource`.

## Folder map
```text
backend/
  app/
    main.py            FastAPI app; includes routers from routes/
    routes/batches.py  POST /batches, GET /batches/{id}[/preview|/events], PATCH .../mapping,
                       POST /batches/{id}/start
    routes/runs.py     GET /runs, GET /runs/{id}[/events], POST /runs/{id}/resume
    events.py          in-process pub/sub (topics run:<id>, batch:<id>) + SSE formatting
    seller.py          loads seller_profile.yaml
    ingest.py          CSV parse, header aliases, cleaning, row checks, in-file dedupe
    config.py          settings from env / backend/.env
    db.py              engine, create_db_and_tables, get_session
    models.py          SQLModel tables + RunStatus enum (Signal/Draft tables are
                       SignalRecord/DraftRecord to avoid clashing with schemas.py)
    schemas.py         Pydantic stage outputs
    llm.py             structured(role, system, user, Schema): Groq -> Gemini, strict
                       JSON schema, retries, one corrective retry on schema errors
    search.py          search() / extract(): Tavily -> DuckDuckGo -> Exa, 24 h cache
    fetch.py           httpx + trafilatura page text (blocks linkedin.com)
    fixtures.py        DEMO_MODE record/fixtures store + response cache
    pipeline/
      runner.py        Stage / RunContext / StopRun, run_one, run_batch, global
                       semaphore, per-account shared research, restart recovery
      stages.py        the stage list, in order (real stages + remaining stand-ins)
      stubs.py         stand-in stages (0.3 s each), used by runner/API tests only
      intake.py        stage 1: recently-researched block + internal context (no LLM)
      resolve.py       stage 2: identity; code verifies the model's evidence
      research.py      stage 3: category searches, relevance gate, page reads
    context_adapter.py CRM / sequence-history lookups (seed tables; swap for HubSpot here)
      extract.py       stage 4: AI-instruction scan (regex + classifier, strip unsafe text),
                       extraction per source, code checks (evidence on page, dates, speaker,
                       duplicate clusters, ghost jobs), stores signals
      score.py         stage 5: model rates relevance/specificity; code adds recency,
                       verifiability, weights, exclusions (SCORE_ADJUSTERS / HOOK_EXCLUSIONS
                       are the hook points for T13/T14); smart model picks 1 of top 3
      draft.py         stages 6-7: smart-model draft (hooked / role_based / outline), critic
                       code checks + fast-model genericness, one rewrite, warnings kept
  tests/               pytest; tests/fixtures/ holds recorded search results
  scripts/             CLI helpers (smoke tests, seeding, demo checks)
frontend/
  app/                 page.tsx = dashboard; batches/, runs/ routes
  components/          shadcn/ui in components/ui
  lib/                 api client, shared types, status colours
seed/                  context.json (fake CRM data); demo CSVs land here in T17
seller_profile.yaml    what "we" sell, ICP, tone, personas, competitors, regions
ASSUMPTIONS.md         every assumption made; add to it as you build
docs/GUIDE.md          the spec
```

## Commands
- `make setup`: create `backend/.venv` with python3.15, install backend and frontend deps
- `make dev-api`: FastAPI on http://localhost:8000 (`GET /health`)
- `make dev-web`: Next.js on http://localhost:3000
- `make test`: backend pytest suite
- `cd backend && .venv/bin/python -m scripts.smoke [--all]`: live check of search, extract
  and one structured LLM call (about 2 Tavily credits); `--all` tries each provider alone
- `cd backend && DEMO_MODE=record .venv/bin/python -m scripts.run_prospects <csv> --names "Amy Asali" --until gather --out x.json`:
  run chosen rows through the pipeline on a throwaway DB (record, or replay with DEMO_MODE=fixtures)
- `cd backend && .venv/bin/python -m scripts.record_extraction`: re-record the real model's answers
  for tests/extraction_case.py (do this after changing a stage-4 prompt)
- `cd backend && .venv/bin/python -m scripts.record_pipeline_case`: re-record stages 4-7 on Priya's
  case (after changing a scoring/draft/critic prompt); tests/test_draft.py replays it
- `make seed`: load `seed/context.json` (fake CRM / sequence history) into the database; idempotent
- Backend Python is always `backend/.venv/bin/python`; there is no uv.

## Conventions
- Every pipeline stage is `async def run(ctx) -> BaseModel` and returns a Pydantic model
  (give it a `message` field: it becomes the stepper's one-line result). To end a run early
  raise `StopRun(status, reason, payload)`; any other exception fails only that run. Report
  LLM/search usage with `ctx.add_usage(tokens, credits)`. Company-level research goes
  through `ctx.batch.company_research(ctx.account_key, factory)` so it runs once per account.
  Code owns the flow; the LLM only makes judgements inside a stage.
- All LLM calls go through `app/llm.py`. All search and fetch go through `app/search.py`.
  No direct `openai` / `tavily` / `ddgs` / `exa` / `httpx` calls elsewhere.
- Web text is untrusted. It always reaches the model inside `<untrusted_source>` tags with
  a system rule never to follow instructions found there. `public_bio` is untrusted too;
  rep `notes` are trusted.
- Every factual sentence in a draft maps to a signal with a source URL. No source, no claim.
- The app never sends email.
- Run statuses come only from the `RunStatus` enum in `app/models.py`.
- Use `DEMO_MODE=fixtures` for repeated runs so tests do not burn search credits. Fixtures
  cover LLM calls too, so a recorded run replays with the network off.
- Free-tier quirks (measured, keep in mind when writing prompts): Groq rejects replies that
  break a strict schema with 400 `json_validate_failed` (llm.py retries once with the error);
  gpt-oss tends to invent enum values, so list allowed values in prompts. gpt-oss-20b
  sometimes answers in prose (400 `output_parse_failed`, also retried). Optional fields in an
  LLM schema need `Field(default=None, ...)`: Gemini omits null fields. Groq's per-minute
  token limit bites with 5 parallel runs; llm.py honours short `retry-after` waits.
  ddgs time filters and combined backends are unreliable; search.py tries engines one by one.
- Any date the model or pipeline relies on comes from `settings.today or date.today()`, and
  never goes into a prompt (recordings must replay on any later day).
- Recording fixtures: record each scenario in a fresh database (prompts carry signal ids) and
  its own fixtures folder (identical prompts would overwrite each other). Failed model calls
  are recorded too and replay as failures.
- Tests never touch the network: conftest blocks it and uses the stand-in stages by
  default; mock `search.search` / `search.extract` / `llm.structured` in stage tests.

## Git
- Push to https://github.com/priyanshuvaidya360-afk/Zamp_SDR (`origin`, branch `main`).
- The repo is public: never commit `backend/.env`, API keys, or `*.db`. Check
  `git status` before every commit.
- One commit per task, e.g. `T05 runner + SSE`, after its "Done when" checks pass.
