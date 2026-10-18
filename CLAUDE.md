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
- LLM: Claude API via `anthropic`. `claude-sonnet-5-5` for hook choice and drafting,
  `claude-haiku-5-5` for extraction and classification.
- Search: Tavily (primary), Exa (fallback). Never scrape LinkedIn.
- Frontend: Next.js 16 (App Router, TypeScript), Tailwind v4, shadcn/ui. Read
  `frontend/AGENTS.md` before writing frontend code: Next 16 differs from older versions.
- Live updates: SSE (`text/event-stream`) + browser `EventSource`.

## Folder map
```text
backend/
  app/
    main.py            FastAPI routes + SSE
    config.py          settings from env / backend/.env
    db.py              engine, create_db_and_tables, get_session
    models.py          SQLModel tables + RunStatus enum (Signal/Draft tables are
                       SignalRecord/DraftRecord to avoid clashing with schemas.py)
    schemas.py         Pydantic stage outputs
    llm.py             Claude client, JSON-schema tool output, retries
    search.py          Tavily/Exa adapter + cache + DEMO_MODE
    pipeline/
      runner.py        orchestrates stages, emits events
      resolve.py       stage 2: identity
      research.py      stage 3: search + fetch
      extract.py       stage 4: AI-instruction scan + extraction
      score.py         stage 5: scoring + hook choice
      draft.py         stages 6-7: writer + critic
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
- `make seed`: load `seed/context.json` (fake CRM / sequence history) into the database; idempotent
- Backend Python is always `backend/.venv/bin/python`; there is no uv.

## Conventions
- Every pipeline stage is `async def run(ctx) -> BaseModel` and returns a Pydantic model.
  Code owns the flow; the LLM only makes judgements inside a stage.
- All LLM calls go through `app/llm.py`. All search and fetch go through `app/search.py`.
  No direct `anthropic` / `tavily` / `exa` / `httpx` calls elsewhere.
- Web text is untrusted. It always reaches the model inside `<untrusted_source>` tags with
  a system rule never to follow instructions found there. `public_bio` is untrusted too;
  rep `notes` are trusted.
- Every factual sentence in a draft maps to a signal with a source URL. No source, no claim.
- The app never sends email.
- Run statuses come only from the `RunStatus` enum in `app/models.py`.
- Use `DEMO_MODE=fixtures` for repeated runs so tests do not burn search credits.

## Git
- Push to https://github.com/priyanshuvaidya360-afk/Zamp_SDR (`origin`, branch `main`).
- The repo is public: never commit `backend/.env`, API keys, or `*.db`. Check
  `git status` before every commit.
- One commit per task, e.g. `T05 runner + SSE`, after its "Done when" checks pass.
