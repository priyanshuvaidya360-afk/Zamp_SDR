# Prompt: complete technical brief of "Signal" (PS-3 GTM outreach agent)

<!-- Paste everything below this line into Claude. -->

You are going to help me understand, explain and improve an AI agent I have built. Below is a complete, accurate brief of the system as it exists today: the use case, the architecture, every pipeline stage, the models and prompts, the data model, the edge cases, the UI, testing, configuration and known limitations.

How I want you to use it:
- Treat this brief as the source of truth about my system. When you answer, refer to the real names used here (files, stages, statuses, settings).
- If I ask about something this brief does not cover, say that it is not covered instead of guessing, and tell me which file I should look at.
- When I ask "why" a design choice was made, answer from the design principles and trade-offs listed here.
- When I ask you to prepare me for an interview or a demo, explain things in plain language first (for a non-technical buyer), then the technical depth.
- Never suggest features that send email automatically; the system is deliberately human-in-the-loop.

---

## 1. What the product is and who it is for

**Name:** Signal (repo: `Zamp_SDR`). Built for the PS-3 "GTM AI agent" assignment.

**User:** an SDR / growth rep who has a list of prospects and needs a researched, personal first-touch email for each one.

**What it does, in one paragraph:** the rep uploads a CSV of prospects (first name, last name, and a company domain or LinkedIn URL; optionally company, title, email, notes, public bio). For every row a fixed pipeline confirms who the person is, researches the public web about them and their company, extracts dated and quoted business "signals", scores them, picks ONE hook, and writes a short cited email (under 120 words). Every step streams live to the browser. Drafts wait in a review queue where a human approves, edits or rejects. **Nothing is ever sent**; the end state is an approved draft that can be exported as CSV.

**Output per prospect:** subject + body, the hook used, the source URL behind every factual sentence, a confidence score, and a status (ready for review, needs input, low signal, held, blocked, suppressed, failed...). Plus a batch summary, a dashboard and an export of approved drafts.

**Design principles (say these in a demo):**
1. **Grounded or nothing:** every factual sentence in a draft traces to a fetched source URL. No source, no claim.
2. **One hook, not five:** the system picks the single most relevant signal and records why it beat the others.
3. **Fail visibly:** thin or ambiguous research produces an honest status ("needs input", "low signal") instead of a generic email.
4. **Human stays in the loop:** nothing sends; approve / edit / reject is the end of the process.
5. **Every stage is inspectable:** inputs, outputs, timings, queries, sources, model output and credits are stored and shown.
6. **Code owns the flow; the model only makes judgements inside a stage.** Thresholds, rules, checks and the order of steps are code; LLMs extract, rate, choose and write.

**The seller ("we"):** a fictional company, **Halyard**, defined in `seller_profile.yaml`: an outbound pipeline engine for B2B SaaS teams that spots accounts with buying signals, finds contacts and drafts researched, cited first-touch emails so growth teams build pipeline without adding SDR headcount. The rep is "Priyanshu Vaidya, Account Executive". The profile also holds the ICP (industries, company size 20-2,000, regions, buyer titles, triggers such as funding, hiring SDRs/AEs, new marketing/sales leaders, launches), `hiring_focus` keywords, tone (plain, specific, no hype; max 120 words; banned phrases like "I hope this finds you well", "quick question", "synergy"), personas by title with the angle each cares about, a send order for buying committees, competitors (Apollo, Clay, 6sense, ZoomInfo, with contract length and an optional migration story) and regions (DE marked strict-consent).

---

## 2. Tech stack and hard constraints

- **Backend:** Python (targets 3.15; runs on 3.12+, developed on Windows with 3.13), FastAPI, SQLModel (SQLite locally at `backend/signal.db`, Postgres via `DATABASE_URL`), Pydantic + pydantic-settings, httpx, tenacity, pandas, scikit-learn, trafilatura.
- **Orchestration:** LangGraph `StateGraph` (one node per stage).
- **LLM:** LangChain `ChatOpenAI` against OpenAI-compatible endpoints. **Groq is primary**: "smart" role = `openai/gpt-oss-120b`, "fast" role = `openai/gpt-oss-20b`. **Gemini is the fallback**: `gemini-3.6-flash` (smart) / `gemini-3.5-flash-lite` (fast). Code always asks for a role, never a model id.
- **Search:** Tavily (primary, 1,000 free credits/month) → DuckDuckGo via `ddgs` (no key) → Exa (optional, only with a key). LinkedIn is never fetched or scraped.
- **Frontend:** Next.js 16 (App Router, TypeScript), Tailwind v4, shadcn/ui (Base UI flavour). Design system: navy chrome (`--navy #0b2a66`), blue actions (`--primary #1a56db`), blue-grey surfaces, Plus Jakarta Sans for UI and Source Serif 4 for the email body. Status colours: green = ready/approved, amber = needs input/low signal/held, red = blocked/failed, blue-grey = running.
- **Live updates:** Server-Sent Events (`text/event-stream`) + browser `EventSource`.
- **Zero cost:** every service is a free tier with no credit card. No paid service may be added.
- **Never sends email.**

---

## 3. Repository map (what lives where)

```text
agents/                 every word any model reads (one .md per agent) + README + PIPELINE_DIAGRAM.md (generated diagram)
  stage2_identify_person.md stage4_extract_signals.md stage5_score_and_pick_hook.md stage5b_buying_committee_plan.md stage6_write_and_check_email.md stage8_recheck_stale_facts.md
backend/app/
  main.py               FastAPI app; on startup creates tables (+ adds missing columns) and fails interrupted runs
  config.py             every tunable number (models, caps, weights, thresholds) from env / backend/.env
  llm.py                the ONLY way to call a model: structured(role, system, user, Schema)
  prompts.py            loads agents/*.md sections; strict {placeholder} filling
  search.py             the ONLY way to search / read pages: Tavily -> DDG -> Exa, 24 h cache
  fetch.py              httpx + trafilatura page text; blocks linkedin.com
  fixtures.py           record / replay of every search and model call (DEMO_MODE) + response cache
  simweb.py             a simulated web for the fictional demo companies (used only while recording)
  ingest.py             CSV parsing, header aliases, cleaning, row validation, in-file dedupe
  context_adapter.py    CRM / sequence-history lookups (seed tables; the swap point for HubSpot)
  seller.py             loads seller_profile.yaml
  models.py             database tables + RunStatus / StageState enums
  schemas.py            shared Pydantic shapes (Signal, HookChoice, Draft, ClaimRef)
  events.py             in-process pub/sub (topics run:<id>, batch:<id>) + SSE formatting
  review.py             stage 8: approve / edit / reject, revalidate on approve, factual gate, CSV export
  routes/               batches.py, runs.py, stats.py, dev.py
  pipeline/
    graph.py            the LangGraph graph
    stages.py           the ordered stage list + STAGE_INFO (who decides, which prompt file)
    runner.py           RunContext, StopRun, run_one / run_batch, concurrency, shared research, resume, retry, recovery
    intake.py resolve.py research.py extract.py score.py account.py draft.py   the stages
    crowded.py competitors.py similarity.py                                   edge-case helpers
seed/                   fake CRM data, demo CSVs, expected demo outcomes, simulated-web pages
seller_profile.yaml     what we sell (see section 1)
frontend/app            upload, batch (prospect list), run view, review queue, dashboard
frontend/components     review-controls, retry-button, status-chip, flow-steps, mapping-dialog, ui/
frontend/lib            api.ts (only place that calls the backend), types.ts, status.ts, stages.ts, use-event-stream.ts
docs/PROJECT_SPEC.md           the spec;  ASSUMPTIONS.md  every assumption made
```

---

## 4. End-to-end user flow (4 screens)

1. **Upload** (`/`): drag-and-drop CSV, "Use demo CSV", template download, recent batches. The seller profile is shown read-only.
2. **Map columns** (dialog): detected column mapping with dropdowns; counts of valid / invalid (with reasons) / duplicate rows.
3. **Prospect list** (`/batches/[id]`): one row per prospect with live stage, a status chip that shows the signal level once research is done (High / Moderate / Low / No signal), hook type; a "Draft email" button per row or "Draft all"; account plans for multi-person companies; batch diversity score; export; review queue link. Failed rows show their reason and a **Retry run** button.
4. **Live run view** (`/runs/[id]`), three columns: **Agent activity** (8-stage stepper, each stage expandable to show queries, sources, model output, tokens, credits, duration; a retry line when a run was retried), **Email** (letter layout, each claimed sentence underlined and numbered to its source; approve / edit / reject controls), **Sources / Signals / Content guide** (sources: profile links, every page read with what it gave, and every other result found with why it wasn't used, LinkedIn as links only; opens on Sources when no signal was found; signal cards with score breakdown; content guide with length, subject, sourced facts, personal score, **factual accuracy**, critic checklist, confidence formula). Needs-input runs show a card per failure reason with the checks behind it and the actions that fit (pick a person, fix the row, search again, company-level draft, dismiss). Already-drafted runs show the earlier draft with a link to review it. Failed runs show a red notice with Retry.
5. **Review queue** (`/batches/[id]/review`): steps through ready drafts; keys a = approve, e = edit, r = reject, j/k = next/previous.
6. **Dashboard** (`/dashboard`): batches and prospects this week, % ready for review, approval rate, median run time, average credits per prospect, "why not ready" breakdown (needs input split by identity reason, low signal, blocked, suppressed, held, failed), dismissed count, retryable failed runs, drafts needing a factual fix, batch and run tables with filter and search.

---

## 5. Architecture

```text
CSV -> ingest (clean, validate, dedupe) -> column mapping -> create one Run per valid row
   -> run_batch: each prospect through the LangGraph graph, max 5 at a time (global semaphore)
        intake -> resolve -> gather -> extract -> score -> account_plan -> draft -> critic -> finish
        (any stage may end the run early with a named status; later stages are marked "not run")
   -> batch-level step: draft similarity + hook-source cap (may re-run some prospects from score)
   -> review queue (approve / edit / reject; re-check old facts on approve) -> export CSV
Every stage event is saved to the database, then published over SSE to the run and batch views.
```

**How LangGraph is used:** `pipeline/graph.py` builds a linear `StateGraph`: one node per stage plus a `finish` node, `START -> intake`, and after every node a conditional edge: next stage, or `END` if the run has a status. The graph state is minimal (`{status, reason}`); all real data lives in a `RunContext` object passed through the graph config: the cleaned row, the seller profile, every stage's output (`ctx.outputs[stage]`), usage counters, confidence, final status/reason, and a shared `BatchContext`. There is no LangGraph checkpointer: persistence is the `stage_events` table, which stores every stage's full output. Parallelism happens inside nodes (`asyncio.gather`) and across prospects, not as graph branches.

**What each node does around its stage:** saves a `started` event, runs the stage, then saves `done` (with output, tokens, credits, duration), `done` with a stop status (StopRun), or `failed` (any other exception, which fails only that prospect, never the batch).

**Stage contract:** every stage is `async def run(ctx) -> BaseModel`; the output model has a `message` field that becomes the stepper's one-line result. To end a run early a stage raises `StopRun(status, reason, payload)`. Stages report usage with `ctx.add_usage(tokens, credits)`.

**Shared per-account work:** company research, company extraction, the crowded follow-up search and the account plan run once per company per batch (`ctx.batch.company_research(key, factory)`); colleagues await the same result. Failures are not cached.

**Concurrency:** `max_concurrent_runs = 5` across all batches; up to 3 extraction calls in flight per run; the account-plan stage releases its slot while it waits for colleagues.

---

## 6. The pipeline, stage by stage

### Stage 1 — Intake and guardrails (code only, no model, no credits)
- **Recently researched:** the same person (LinkedIn URL, email, or first+last name+domain) in another batch within 30 days (non-failed). If that run wrote a draft → `already_drafted`: the run shows that draft with a link to review it there (one email is never queued twice). Otherwise → `blocked`.
- **Account context** from seed CRM tables via `context_adapter`: past sequence touches, reply outcomes, open opportunities, CRM account owner.
- **Rules:** this person replied "not interested" / "unsubscribed" → `suppressed` (case 1); open opportunity at the domain → `held`, routed to the deal owner (case 2); this person replied "not now" → message type becomes **follow-up** and the reply snippet is quoted later (case 1).

### Stage 2 — Resolve identity (search + fast model + code checks)
- Up to 4 searches over a 10-year window: `"First Last" site:domain`, `"First Last" "Company"`, `Name Title Company`, `"linkedin_url" Name` (deduped).
- The fast model lists candidate people from the snippets (each with title, company, domain, location, country, evidence URL, evidence, confidence).
- **Code verifies, it does not trust the model's confidence:** evidence URL equal to the prospect's LinkedIn → ≥0.9; evidence URL not among the search results (made up) → capped at 0.5; evidence that doesn't name both the person and the company → capped at 0.75. Same name + same employer entries are merged.
- **Decision (code):** exactly one candidate ≥ 0.8 with no rival ≥ 0.5 → continue; otherwise `needs_input`.
- **Why it failed (code, `diagnose`):** `search_failed` (every provider errored; "no results" still counts as working) · `bad_data` (placeholder name like Test/asdf/John Doe/digits, or a domain that doesn't connect while nothing mentions it; we never claim a person doesn't exist) · `not_public` (real company, person never appears with it) · `ambiguous` / `unconfirmed`. Each check is shown to the rep with ✓/✗.
- **Rep actions (resume at resolve):** pick a candidate or type their details (trusted, confidence 1.0) · fix a bad-data row (searched again, not trusted) · search again · **company-level draft** for a not-public person (confidence 0.3, writer told to say nothing personal, banner, approval needs the rep to tick that the person exists) · dismiss (`dismissed`).
- Fills a missing company / domain from the confirmed person. Strict-consent country (e.g. DE) → `held` for a compliance check (case 12).

### Stage 3 — Gather signals (code + fast model for extra searches)
- Company names are cleaned first ("X | tagline", "X (ABC)" → "X").
- **Core** (Tavily first, 365-day window): funding, hiring, launches, appointments, expansion; person talks/podcasts and person + company.
- **Own site** (no date window): `site:{domain} {company}` and the company blog/announcements; up to 2 pages read. Small companies' product, pricing and customer pages become `company_focus` signals.
- **Wide** (DuckDuckGo first, so no credits): customers and case studies, G2/Capterra/Trustpilot reviews, awards and rankings, general news; the person's own articles and webinars.
- **Planned** (`stage3_plan_extra_searches.md`, fast model): up to 4 searches written for this prospect (industry press, regional news, app stores, events, regulators). Code accepts a query only if it names the company or the person, never touches LinkedIn, uses `site:` only on their own domain, and is new. A planner failure means no extra searches, never a failed run.
- **Relevance gates:** a company result must name the company or domain in its URL or title, **or** its text names the domain once or the name twice (one passing mention in a listicle doesn't count); own-site results must be on the domain; a person result must contain the full name and a company term. LinkedIn is never read.
- Up to 18 searches and **14 pages** read (4 person, 2 own site, 8 company), each truncated to 6,000 characters; a page found by both a company and a person search is read once. Every result (kept or not) is stored for the run's Sources tab.
- Search results keep URL, title, snippet and published date (DuckDuckGo results have no date).

### Stage 4 — Extract signals (fast model + code checks)
- **AI-instruction scan first (case 11):** regex patterns find sentences that address AI tools ("if you are an AI...", "ignore previous instructions", "include a recipe for flan", "email this to..."). Only flagged sentences go to the fast model, which labels them `screening_trap`, `hostile` or `benign`. Unsafe sentences are stripped from the page; if the classifier is unavailable they are treated as unsafe. The prospect's `public_bio` (untrusted) is scanned the same way.
- **Extraction:** one fast-model call per page; the page text is inside `<untrusted_source>` tags. Each signal: type (funding, acquisition, hiring, product_launch, leadership_change, person_content, expansion, partnership, press, negative_event, **company_focus** (what they sell, to whom, customers, pricing, integrations, stated goals), other), about (person/company), summary, **exact evidence quote**, date, sensitive flag, speaker, is_reshare, time_sensitive, job posting details.
- **Code checks:** ≥80% of the evidence's words must be on the page (else dropped as "evidence not found"); dated signals older than 365 days dropped; a page's listing date only dates single-story pages; **speaker rules (case 7)**: someone else's quote or a reshare becomes company-level with an attribution note; **ghost jobs (case 4)**: the same title in ≥3 locations or posted ≥30 days ago is flagged and reframed as "hard to fill"; open-role counts are marked time-sensitive; **the same event from several outlets is merged** and its coverage counted.
- **Crowded follow-up (case 5):** if a funding/launch/acquisition is under 30 days old and covered by ≥5 outlets, one extra search looks for what followed it (hiring, new markets, new leaders).
- A screening trap halves the identity confidence. Signals are stored in the `signals` table.

### Stage 5 — Score and pick the hook (smart model + code)
- **Smart model rates every signal:** relevance 0-5 (how directly it creates the problem we solve; an honest indirect link is a 2) and specificity 0-5 (about this person > their team > company or what it says about itself > industry). Company-level specificity is capped at 4. The prompt asks for fair, not harsh, ratings: a true, specific fact is useful even when it isn't news.
- **Code formula:** `score = 0.35·relevance + 0.25·specificity + 0.25·recency + 0.15·5·verifiability + adjustments`, where `recency = 5·e^(−days/120)` (undated = 120 days) and `verifiability` = 1 for the company's own site, a reputable outlet or ≥2 outlets, else 0.5.
- **Adjusters / exclusions:** crowded event −1.5 and "context only, never the opener" (case 5); never a hook: AI-screening findings, sensitive news (layoffs, lawsuits, breaches...), competitor mentions (case 6), sources excluded by the batch balance or a failed re-check.
- **Competitor usage (case 6):** adopted a competitor within 12 months → `held` with a renewal reminder; established user + migration story → the displacement angle becomes the hook; no migration story → `held`.
- No eligible signal or best score < 1.5 → **low signal** (the run continues and gets a clearly labelled role-based draft). The thresholds are deliberately flexible: a good-enough true fact can open an email; the ethics rules above are never loosened.
- Otherwise the smart model picks ONE of the top 3 (returning "no hook" only if every signal is unrelated to what we sell), writes the angle, lists runners-up and says why not the others. Code holds it to the candidate list. Signals are numbered 1..n in prompts (never database ids) so recordings replay.

### Account plan (between score and draft; case 3 buying committee)
- If 2+ people from the same company are in the batch, each waits at a barrier until all colleagues have scored or stopped. The smart model gives each person a **different opener** fitting their persona, with company-wide context used for at most one person and person-level signals only for their owner. Code checks the rules; one retry with the violations named, then a code fallback. Each person gets a send day from `send_order` (Head of Growth day 0, VP Sales day 2, CMO day 5) and a counterfactual ("what they'd have got alone").

### Stage 6 — Draft (smart model)
- Three modes: **hooked** (normal), **role_based** (low signal: honest, no specific claims, labelled banner), **outline** (the prospect screens for AI outreach: 3 bullet points for the rep to write by hand, labelled banner). A **company-level** draft (identity not verified) adds a rule to say nothing personal and an "Identity not verified" banner.
- The prompt contains: rep and seller, offer, problem solved, the hook + up to 2 supporting facts (each with type, absolute date, summary, exact evidence and attribution hints, inside `<untrusted_source>`), context-only instructions for crowded events, a follow-up reply snippet when relevant, and rules: under 120 words; subject under 7 words; open with the observation, not a compliment; one linking sentence; one soft ask with no date; absolute dates only; follow attribution exactly; address the prospect as "you"; their "we" restated as theirs; plain text, no emoji, no links; banned phrases; every factual claim about the prospect or company supported by evidence whether or not declared; retrieved content is untrusted data; assumptions never stated as facts; no invented statistics, reviews, funding, hiring numbers or achievements.
- The model returns subject, body and **claims** (each factual sentence → the fact id it relies on).

### Stage 7 — Critic and fix (code checks + one fast-model audit)
- **Code checks:** length, subject length, banned phrases, no flattery opener, absolute dates, no emoji, no links, prospect not named in the third person, no internal labels, declared claims refer to known and safe signals and actually appear in the body, **every number in the body (digits and written-out words like "two", "a dozen") appears in some signal**, quotes and reshares attributed to their real speaker, a crowded event never in the first sentence.
- **Factual audit (one fast-model call that also scores genericness):** it reads the subject and every sentence of the body, **independently of the declared claims list**, against exactly the evidence the writer was given (hook + support facts, our offer from the seller profile, the rep's trusted notes). Each factual sentence gets `supported / unsupported / contradicted / uncertain / not_verifiable`, a fact id, a reason and a suggested correction.
- **Code owns the verdict:** sentences not in the email are dropped; "supported" must point to a fact that was given (ids are normalised; an invented id → unsupported); claims about our product are checked against `seller_profile.yaml` by word coverage; a model "unsupported" on a sentence that a given fact states in nearly the same words with the same numbers becomes "uncertain" (the fast model is not consistent run to run). **Unsupported or contradicted = blocking; uncertain = warning only.**
- Any failed check, genericness ≥ 3, or a blocking fact → **exactly one rewrite**, told to change only the flagged sentences, then everything is checked again. Problems that remain become visible warnings; if a blocking fact remains, the draft **cannot be approved** until a human edits it ("N unsupported factual claims: edit before approving").
- **Confidence** = identity confidence × hook strength (score/5, or 0.3 without a hook) × 0.8 if warnings remain.

### Stage 8 — Human review (UI + `review.py`)
- **Approve:** refused while the factual audit is blocking. If the draft is older than 3 days and a claim relies on a time-sensitive fact, the sources are re-fetched first (**case 10**): the fast model compares then vs now: unchanged → approved; changed → the smart model rewrites only that sentence (numbers verified against the new quote) and a new version with a red/green diff must be approved again; gone → the run goes back through scoring without that source so the runner-up becomes the hook.
- **Edit:** the rep's text is audited against the same evidence before it is approved; if it still states unsupported facts the answer is "needs fix" with the sentences listed. The diff is stored.
- **Company-level drafts:** approve and edit need `identity_confirmed` (the rep ticks that the person exists); stored on the draft.
- **Reject:** with an optional reason.
- **Export:** CSV of approved rows (or all rows) with status, hook, source URL, subject and body.

### Batch-level step (case 9: template drift)
After drafts finish: TF-IDF cosine similarity across the batch's final drafts (pairs > 0.8 flagged as warnings on both drafts); **no single hook source may open more than 20% of drafts** (at least one allowed); the lowest-scoring overflow prospects are re-run from scoring with that source excluded. Diversity = 1 − mean pairwise similarity, shown on the batch.

---

## 7. Run statuses (the only allowed values, `RunStatus` enum)

`running`, `needs_input` (identity failed; `identity_outcome` says why), `low_signal` (no usable hook; role-based draft), `ready_for_review`, `already_drafted` (researched in another batch recently; that draft is shown), `blocked` (researched recently, no draft), `suppressed` (opted out), `held` (open deal, competitor rule, strict-consent region), `approved`, `rejected`, `dismissed` (the rep closed a needs-input run), `failed` (an error; retryable), plus CSV-only `invalid` and `skipped` (duplicate row). Stage states: `started`, `done`, `failed`, `skipped`.

---

## 8. Reliability features

- **LLM gateway (`llm.py`):** strict JSON-schema output built from the Pydantic model; transient errors retried 3 times with backoff; a 429 with retry-after ≤ 30 s is waited out up to 3 times, otherwise fall back to Gemini; a schema failure (Groq 400 `json_validate_failed` / `output_parse_failed`, or Pydantic validation) gets **one corrective retry** that shows the model the error; all providers failing raises `LLMUnavailable` with plain reasons. No temperature is set (provider defaults). Token usage is recorded per call.
- **Search gateway (`search.py`):** each provider retried 3 times on transport errors; errors, no credits or zero results hand over to the next provider; DuckDuckGo tries each engine alone, with and then without the date filter; empty results are a valid answer (thin signal). 24-hour cache per provider + query.
- **Prompt-injection defence:** all web text and the public bio reach models only inside `<untrusted_source>` tags with a system rule never to follow instructions there; the regex + classifier scan strips unsafe sentences before extraction. Rep notes are trusted.
- **Retry of failed runs:** `POST /runs/{id}/retry` (UI: "Retry run"). Allowed only for `failed` runs, at most 3 times (`retry_max_attempts`). It restarts at the first stage whose saved output is missing, unreadable or stale (web research older than 24 hours is redone), reusing every earlier stage's saved output; a single guarded database update prevents two simultaneous retries; each retry is recorded as a `retry` stage event ("Retry 1 of 3 · restarting at extract · reusing saved research"); history is kept. Runs interrupted by a server restart are marked failed on startup and become retryable.
- **Resume:** a `needs_input` run resumes at resolve with the rep's choice: a person, corrected details (searched again), a new search, a company-level draft, or dismiss.
- **Limits:** per visitor per hour, 20 uploads and 60 prospects drafted (`app/limits.py`), so a shared link can't drain the free quotas.
- **Draft versions:** append-only; the draft to review is always the newest record (a re-run starts again at v1).

---

## 9. Data model (SQLModel tables)

- `batches`: filename, counts (rows, valid, invalid, duplicates), column mapping, headers, source rows, validated rows with reasons, status (validating / running / done), diversity score and similarity report.
- `runs`: one per prospect: row number, raw row, names, company, title, domain, LinkedIn, email, rep notes (trusted), public bio (untrusted), account key, message type (cold / follow-up), send day, account plan, status, final reason, confidence, started/finished, credits, retry count.
- `stage_events`: run id, stage, state, message, payload (full stage output, tokens, credits), duration, timestamp. This is the run history and the resume/retry source.
- `signals`: per run: type, about, summary, evidence, source URL, date, sensitive, speaker, is_reshare, time_sensitive, coverage, ghost_suspect, note, cluster URLs, full score breakdown, selected flag, rejection reason.
- `drafts`: per run, versioned: subject, body, hook signal, claims, critic report (checks, problems, genericness, factual audit with its evidence, warnings, confidence formula, mode, banner), reviewer action, edited body, review data (diff, re-check results, edit audit).
- Seed CRM tables: `crm_accounts`, `opportunities`, `sequence_history` (loaded from `seed/context.json`).
New columns are added automatically on startup (additive migrations only).

---

## 10. API

`POST /batches` (upload) · `GET /batches` · `GET /batches/{id}[/preview|/events]` · `PATCH /batches/{id}/mapping` · `POST /batches/{id}/start` · `POST /batches/{id}/draft` ({row_numbers} or all remaining; a row with a run is not run twice) · `GET /batches/{id}/export.csv[?all=true]` · `GET /runs` (status filter, name search, paginated) · `GET /runs/{id}[/events]` · `POST /runs/{id}/resume` · `POST /runs/{id}/retry` · `POST /runs/{id}/review` (approve / edit / reject; outcomes approved, edited, rejected, changed, gone, needs_fix) · `GET /stats` · `GET /seller` · `GET /health` · `POST /dev/age-draft/{draft_id}` (demo only).
SSE events: `stage`, `run_status`, `batch_progress`, `batch_status`, `end`. Events are saved before they are published, so a late client replays from the database and continues from the last event id.

---

## 11. Prompts (`agents/*.md`)

Every word a model reads lives in markdown files; code only picks `## sections` and fills `{placeholders}` (a missing placeholder is an error). Editing a file changes the next run without a restart.
Code uses short keys (`draft`); `prompts.AGENT_FILES` maps them to files named by stage and job.
- `stage2_identify_person.md` (fast): match the prospect to real people; confidence bands.
- `stage3_plan_extra_searches.md` (fast): extra searches for this prospect, checked by code.
- `stage4_extract_signals.md` (fast): classify AI-addressed sentences; extract signals with allowed type values listed (open-weight models invent enum values otherwise).
- `stage5_score_and_pick_hook.md` (smart): rate relevance/specificity; choose the hook; follow-up preference.
- `stage5b_buying_committee_plan.md` (smart): distinct openers per committee member; retry wording.
- `stage6_write_and_check_email.md` (smart writer + fast audit): intro, offer, problem, hook, facts, attribution lines, context-only, outline, role-based, rules, rewrite, audit sections.
- `stage8_recheck_stale_facts.md` (fast compare, smart sentence rewrite): case 10.
`agents/PIPELINE_DIAGRAM.md` is generated from the LangGraph graph (`scripts.draw_pipeline`); a test fails if it is stale.

---

## 12. Edge cases (numbered as in the spec)

| # | Case | What happens |
|---|---|---|
| 1 | Warm prospect treated as cold | "not now" → follow-up quoting their reply; opted out → `suppressed` |
| 2 | Live deal collision | open opportunity → `held`, routed to the deal owner |
| 3 | Buying committee in one batch | account plan: different opener per person, persona angles, send days |
| 4 | Ghost job postings | flagged; framed as "hard to fill", not "you're growing" |
| 5 | Crowded hook | penalty, context only, one follow-up search for what came next |
| 6 | Competitor signal | held / displacement angle / never an opener |
| 7 | Borrowed words | speaker + reshare detection; attribution enforced by the critic |
| 8 | Acquired / rebranded company | talking point only (needs enrichment data) |
| 9 | Template drift | batch similarity, 20% hook-source cap, diversity score |
| 10 | Stale draft in the queue | re-check time-sensitive facts on approve; diff; runner-up if gone |
| 11 | AI screening / prompt injection | scan + classifier; outline mode + banner; injection stripped, never followed |
| 12 | Cross-border consent | strict regions → `held` for a compliance check |
Baseline behaviours: ambiguous identity (`needs_input`), thin signal (`low_signal`), sensitive news (never a hook), job change (resolve checks current employer). The four demo cases are 3, 5, 10 and 11.

---

## 13. Configuration (key settings in `backend/app/config.py`)

Models and providers as in section 2 · `llm_timeout_s 60` · rate-limit wait ≤ 30 s × 3 · `max_concurrent_runs 5` · `extract_concurrency 3` · `recent_run_days 30` · `identity_accept 0.8`, `identity_plausible 0.5` · `gather_max_searches 18`, `gather_max_extracts 14`, `gather_planned_queries 4` · `signal_window_days 365` · `source_max_chars 6000` · weights 0.35 / 0.25 / 0.25 / 0.15, `recency_scale_days 120`, `undated_days 120`, `low_signal_below 1.5`, `high_signal_at 3.0`, `hook_candidates 3` · crowd penalty 1.5, crowded ≤ 30 days and ≥ 5 outlets · ghost job ≥ 3 locations or ≥ 30 days · `draft_max_words 120`, `subject_max_words 6`, `genericness_fail_at 3` · `revalidate_after_days 3` · `retry_max_attempts 3`, `retry_research_max_age_hours 24` · `cache_ttl_hours 24` · `identity_unverified 0.3` · `uploads_per_hour 20`, `prospects_per_hour 60` · `demo_mode` live / record / fixtures · `today` (pins the date for demos and tests; dates never go into prompts).
Secrets (`GROQ_API_KEY`, `GEMINI_API_KEY`, `TAVILY_API_KEY`, optional `EXA_API_KEY`) live only in `backend/.env`, which is git-ignored.

---

## 14. Demo mode, testing and quality

- **DEMO_MODE:** `live` (real calls), `record` (real calls, every search / page / model answer saved as a JSON fixture), `fixtures` (replay only: no network, no keys). Recordings are keyed by the exact prompt text, so after editing a prompt the recordings must be re-recorded (`scripts.record_demo`, `record_pipeline_case`, `record_crowded_case`, `record_stale_case`, `record_extraction`). Recording is resumable after a free-tier rate limit.
- **The demo:** `seed/demo.csv` and `seed/demo_messy.csv` (real and fictional companies; fictional ones served by the simulated web), expected outcomes in `seed/demo_cases.json`; `scripts.check_demo` replays everything with the network off and checks every row (currently passes).
- **Tests:** pytest, network blocked in `conftest`; stand-in stages for runner/API tests; mocked `llm.structured` / `search` in stage tests; recorded real-model cases replayed. Currently 255 tests pass, including retry (eligibility, concurrency, stale research, interrupted runs, history) and factual-audit tests (invented funding, invented hiring numbers, written-out quantities, sentences missing from the claims list, contradictions, one rewrite only, approval blocked, edits re-audited). Frontend: `tsc --noEmit` and `eslint` pass.
- **Commands:** `uvicorn app.main:app --reload --port 8000` (backend; on Windows set `PYTHONUTF8=1`), `npm run dev` (frontend, port 3000), `python -m scripts.smoke` (live key check), `python -m scripts.check_demo`, `python -m pytest -q`.

---

## 15. Observability and cost

- Every stage's input summary, output, queries, sources, page text, model output, tokens, credits and duration are stored and visible in the run view. Runs store total search credits; tokens are per stage event.
- Typical prospect: about 13 Tavily-first searches (identity 4, core 7, own site 2) + 2 page-read calls; the 7-9 wider and planned searches try DuckDuckGo first (no credits), about 13-16 model calls of which 3-5 use the smart model. Company research is shared by colleagues at the same account. All usage is free tier ($0).
- No external tracing is configured (LangSmith is installed but off); application loggers use defaults.

---

## 16. Known limitations and trade-offs (be honest about these)

- The fast model's factual judgements vary run to run; code limits false blocks (uncertain = warning) but some correct sentences may still show "check this".
- Undated signals (common from DuckDuckGo and company sites) survive the 365-day filter with a recency penalty.
- The looser hook rules trade some precision for coverage: a modest company_focus fact can now open an email; the reviewer is the final check.
- The company relevance gate accepts a repeated mention in the text; very generic company names can still let unrelated pages through.
- No contradiction detection between different sources; the re-check on approve catches facts that change over time.
- One extraction call per page is the main token cost; two smart calls in scoring overlap partly.
- Single-process design: the event bus, semaphores and batch contexts are in memory (fine for the assignment; a queue + shared pub/sub would be needed to scale out).
- A retried member of a buying committee is planned alone (colleagues already finished).
- Free tiers cap volume at roughly 50-60 prospects a day; Groq's per-minute token limit slows large parallel batches (handled by waits and fallback).
- Corrected drafts from the 3-day re-check are not audited a second time (the changed sentence is already verified against a quote from the page).
- Deploy is a Render blueprint (`render.yaml`) + Vercel + Neon Postgres on free tiers; the free backend sleeps when idle, and the hourly limits are in memory (reset on restart).

---

## 17. Glossary

**Signal:** a dated, quoted business event about the prospect or their company, extracted from a page. **Hook:** the one signal the email opens with. **Angle:** why the hook matters to this person. **Runner-up:** a candidate hook that lost, with a reason. **Coverage:** number of distinct outlets reporting the same event. **Crowded:** a big, fresh, widely covered event every vendor will mention. **Context only:** may support the email but never open it. **Claim:** a factual sentence in the email mapped to a signal. **Factual audit:** the critic's independent check of every factual sentence against the given evidence. **Blocking:** an unsupported or contradicted fact that prevents approval. **Role-based draft:** an honest email without specific facts, used when no signal qualifies. **Outline:** bullet points for a hand-written email when the prospect screens for AI outreach. **Account key:** normalised domain (or company name) grouping colleagues. **Fixtures:** recorded responses that replay without the network.

---

Start by confirming in a few lines that you understand the system, then wait for my questions.
