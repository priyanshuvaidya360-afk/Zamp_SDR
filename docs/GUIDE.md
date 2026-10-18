# PS-3 Outreach Engine — Build Guide

Oct 18, 2026 · @Shivam Shukla

## What we're building

A web app where an SDR uploads a prospect-list CSV and watches a pipeline research every row, pick one real hook per prospect, and produce cited email drafts that wait in a review queue for human approval. Working name: **Signal** — use whatever you like.

**Input:** a CSV of prospects, one row each (name, company, optional title, URL, notes), plus the seller's context (what we sell, ICP, tone). **Output:** for each row, a draft (subject + body, under 120 words), the hook it uses, the source URL behind that hook, a confidence score, and a status: ready for review, needs human input, or blocked. Plus a batch summary and an export CSV of approved drafts.

What the graders will score, in their own words: it actually runs on real inputs, the edge cases are deliberate, the UI has a live run view and a dashboard, and you can explain every choice to a non-technical buyer. Build for those four, nothing else.

Design principles to say out loud in the demo:

- **Grounded or nothing.** Every claim in a draft must trace to a fetched source URL. No source, no claim.
- **One hook, not five.** The process picks the single most relevant signal and says why it beat the others.
- **Fail visibly.** When signal is thin or ambiguous, the process says so and asks the human, instead of writing a generic email.
- **Human stays in the loop.** Nothing sends. The end state is an approve / edit / reject screen.
- **Every stage is inspectable.** Inputs, outputs, timings and LLM reasoning for each step are stored and shown.

## Input: the prospect CSV

The CSV is the contract with the user, so validate it before spending a single API credit. Accept messy real-world exports (from Apollo, Sales Navigator, a spreadsheet) by mapping header aliases, and report problems per row instead of failing the whole file.

| Column | Required | Accepted header aliases | Used for |
| --- | --- | --- | --- |
| `first_name` | Yes | first, firstname, given\_name | Identity search |
| `last_name` | Yes | last, lastname, surname, family\_name | Identity search |
| `company_domain` | Yes, unless `linkedin_url` is given | website, domain, company\_url | Exact company match; account grouping |
| `linkedin_url` | Yes, unless `company_domain` is given | linkedin, profile\_url | Exact person anchor for identity (searched, never scraped) |
| `company` | Recommended | company\_name, account, organization | Display + search queries; derived from the domain or identity step if missing |
| `title` | No | job\_title, role, position | Disambiguation, relevance |
| `email` | No | work\_email | Dedupe key; carried to export |
| `notes` | No | rep\_notes, context | Rep context, trusted input |

**Why these are mandatory:** first name + last name + a company name alone is not a unique identity. Company names collide ("Acme", "Northwind"), and common names at large companies return several people, which leads to drafts about the wrong person or generic, low-quality output. A company domain pins the company exactly; a LinkedIn URL pins the person exactly. Requiring one of them up front is cheaper than guessing later. If the file only has a single `full_name` column (alias: name, prospect, contact), split it on the last space into first and last name and show the split in the preview so the user can check it.

Sample to build and demo with (the rows map to your happy path and edge cases):

```csv
first_name,last_name,company,title,company_domain,linkedin_url,notes
Priya,Raman,Acme Fintech,VP Finance,acmefintech.com,,
Rahul,Sharma,Infosys,,infosys.com,,
Ananya,Iyer,Stealthco Labs,Founder,stealthco.io,,met at SaaSBoomi
David,Lee,BigCorp Retail,CFO,,https://www.linkedin.com/in/davidlee-cfo,
Meera,Shah,NewCo Payments,Head of Finance Ops,,,
,,Acme Fintech,Controller,acmefintech.com,,
Priya,Raman,Acme Fintech,VP Finance,acmefintech.com,,
```

Row by row: Priya is the happy path; Rahul is valid but a common name at a huge company with no title (identity stays ambiguous → `needs_input`); Ananya is thin signal; David is valid on LinkedIn URL alone; Meera is `invalid` (no domain and no LinkedIn URL); the Controller row is `invalid` (no name); the last row is a `skipped` duplicate.

**File-level checks** (reject the upload with a clear message): not UTF-8 or not CSV, no rows, no columns mappable to first name and last name (or a splittable full name), no column mappable to either company domain or LinkedIn URL, more than 50 rows (a demo-scale cap, stated as an assumption).

**Row-level checks** (row gets `invalid` or `skipped`, the rest of the batch runs):

- Missing first name or last name → `invalid` ("missing last name").
- Missing both company domain and LinkedIn URL → `invalid` ("needs a company domain or LinkedIn URL to identify this person").
- Malformed domain (no dot, contains spaces) or a LinkedIn URL that is not a `linkedin.com/in/...` profile → `invalid` with the reason.
- Exact duplicate inside the file (same LinkedIn URL, same email, or same first + last name + company domain) → second copy `skipped` as duplicate.
- Same person run in the last 30 days → `blocked` (stage 1, as before).
- Trim whitespace, fix casing, strip `www.`, `https://` and paths from domains, normalise LinkedIn URLs to `linkedin.com/in/<slug>`, split a single full-name column into first + last.

Before running, show a **preview**: detected column mapping (user can correct it), row count, and how many rows are valid, duplicate or invalid. The user clicks "Run N prospects" to start. This preview is cheap to build and shows judgement in the demo.

## Process map

This is the map to draw on paper on Day 1 and to show in the interview. Every stage either hands on to the next or exits with a named status; the only loops are the rep's pick (resumes at stage 3) and one critic rewrite.

&#91;embedded content: pipeline · 8 stages, 4 exits, 2 loops\]

Amber exits wait for the rep, red exits stop the run, and only stage 8 ends in a decision — made by a person. A CSV upload fans out into one run per valid row; each row follows this map on its own, so one stuck row never stalls the batch. Account-context checks join stage 1, an account plan sits between stages 5 and 6, and revalidation runs on approve (see Account context below).

## Architecture and stack

Two services: a Next.js app for the UI and a FastAPI service that runs the pipeline and streams each stage to the browser over Server-Sent Events (SSE). Everything else is an API call.

| Layer | Pick | Why this one |
| --- | --- | --- |
| Frontend | Next.js (App Router) + Tailwind + shadcn/ui | Fast to make a clean dashboard; deploys on Vercel in minutes |
| Backend | Python 3.12 + FastAPI + Pydantic | Async pipeline, typed stage outputs, easy SSE |
| LLM | Claude API: `claude-sonnet-5-5` for hook choice and drafting, `claude-haiku-5-5` for per-source extraction | Strong writer for the draft; cheap fast model for the many small calls |
| Web search | [Tavily](https://docs.tavily.com/documentation/api-credits) Search + Extract | Returns page content, not just links; 1,000 free credits a month; basic search = 1 credit, advanced = 2 |
| Backup search | [Exa](https://exa.ai/docs/reference/pricing) | Pay-as-you-go, $7 per 1,000 searches, $20 free credits for new accounts. Swap in if Tavily misses |
| Page fetch | httpx + trafilatura | Clean text from company blog / careers pages |
| Database | SQLite locally, Postgres (Neon or Supabase) deployed, via SQLModel | Same code, swap the connection string |
| Live updates | SSE (`text/event-stream`) + browser `EventSource` | One-way stream is all the run view needs; simpler than websockets |
| Background work | `asyncio` tasks inside FastAPI | No Celery or Redis needed at demo scale |
| Hosting | Vercel (frontend), Render or Railway (backend) | Free or near-free; always-on link for the graders |

**LinkedIn: don't scrape it.** [Proxycurl shut down on 4 July 2025](https://nubela.co/blog/what-is-proxycurl-api-now-in-2026-im-the-founder/) after LinkedIn sued it over fake-account scraping. Use only public web results (news, company blog, podcasts, job boards, public posts that search engines index) plus an optional "paste LinkedIn notes" field the rep fills in. Say this in the interview: it shows you know the legal and reliability risk.

**Budget per run:** cap at 8 searches and 10 page extracts, about 10–20 Tavily credits. The free tier then covers roughly 50–100 runs a month, enough for build, tests and demo. Cache search results by query for 24 hours so rehearsals don't burn credits.

Repo layout:

```text
signal/
  backend/
    app/
      main.py            # FastAPI routes + SSE
      pipeline/
        runner.py        # orchestrates stages, emits events
        resolve.py       # identity resolution
        research.py      # search + fetch
        extract.py       # source -> Signal objects
        score.py         # rubric + hook choice
        draft.py         # writer + critic
      models.py          # SQLModel tables
      schemas.py         # Pydantic stage outputs
      llm.py             # Claude client, JSON-schema helpers, retries
      search.py          # Tavily/Exa adapter + cache
    tests/fixtures/      # saved search results for offline demo
  frontend/
    app/
      page.tsx           # dashboard
      runs/new/page.tsx  # input form
      runs/[id]/page.tsx # live run view + review
    components/
  seller_profile.yaml    # what "we" sell, ICP, tone rules
  ASSUMPTIONS.md
```

## Data model

Five tables carry everything the dashboard and run view need. Store every stage's input and output as JSON so any run can be replayed and explained.

| Table | Key fields | Used by |
| --- | --- | --- |
| `batches` | id, filename, uploaded\_at, row\_count, valid / invalid / duplicate counts, column\_mapping JSON, status (validating / running / done), finished\_at | Dashboard, batch view, export |
| `runs` | id, batch\_id, row\_number, raw\_row JSON, first\_name, last\_name, company, title, domain, linkedin\_url, rep\_notes, status, final\_reason, confidence, started\_at, finished\_at, cost\_credits | Batch view rows, filters, metrics |
| `stage_events` | run\_id, stage, state (started / done / failed / skipped), message, payload JSON, duration\_ms, ts | Live run view, audit trail |
| `signals` | run\_id, type, about (person / company), summary, evidence, source\_url, published\_at, scores JSON, selected bool, rejected\_reason | Signals panel, "why this hook" |
| `drafts` | run\_id, version, subject, body, hook\_signal\_id, critic\_report JSON, reviewer\_action, edited\_body, reviewed\_at | Review queue, export, edit history |

Run status is a closed set; the pipeline sets it, the reviewer moves it on:

| Status | Set when |
| --- | --- |
| `running` | Pipeline in progress |
| `needs_input` | Identity ambiguous, or rep must confirm something |
| `low_signal` | Nothing specific found; a clearly labelled role-based draft is offered |
| `ready_for_review` | Draft passed the critic |
| `blocked` | Only sensitive signals found, or a rule stops outreach (recently contacted, opted out) |
| `approved` / `rejected` | Reviewer's decision |
| `failed` | An API or system error the pipeline could not recover from |

Rows that never start a run get one of two CSV-only statuses: `invalid` (missing first/last name, or neither company domain nor LinkedIn URL, reason stored) or `skipped` (duplicate inside the same file). Keep the original row in `raw_row` so the export can return every column the user uploaded.

Core Pydantic schemas (the LLM returns these via JSON-schema tool calls, so output is always parseable):

```python
class Signal(BaseModel):
    type: Literal["funding", "hiring", "product_launch", "leadership_change",
                  "person_content", "expansion", "partnership", "press",
                  "negative_event", "other"]
    about: Literal["person", "company"]
    summary: str                 # one line, our words
    evidence: str                # short snippet from the source
    source_url: HttpUrl
    published_at: date | None
    sensitive: bool              # layoffs, lawsuits, deaths, breaches

class SignalScore(BaseModel):
    relevance: int               # 0-5: ties to what we sell
    specificity: int             # 0-5: about this person > company > industry
    reasoning: str

class HookChoice(BaseModel):
    signal_id: int | None        # None = no usable hook
    angle: str                   # why it matters to this person
    runners_up: list[int]
    why_not_others: str

class Draft(BaseModel):
    subject: str
    body: str
    claims: list[ClaimRef]       # each factual sentence -> signal_id
```

## Pipeline stages in detail

Eight stages, each a plain async function that takes the run context, returns a typed result, and emits `started` / `done` events. Code decides the flow; the LLM only does judgement inside a stage. That split is the main design choice to defend.

**Batch layer (stage 0, code only).** `POST /batches` takes the file, parses it with pandas (`dtype=str`, `keep_default_na=False`), maps headers by alias, runs the file and row checks from the CSV section, and returns the preview. `POST /batches/{id}/start` creates one `runs` row per valid line and schedules them:

- Run at most 5 prospects at once with an `asyncio.Semaphore(5)`, so you stay inside API rate limits and the run view stays readable.
- Group rows by company domain and research each company once per batch (stage 3's company queries), then share the result. Two people at the same company cost one set of company searches, not two.
- Each row is isolated: an exception marks that run `failed` and the batch carries on.
- The batch is `done` when every run has reached a non-running status; emit a `batch_progress` SSE event after each run finishes (done / total, counts by status).

1. **Intake and guardrails** (code only)
   - Use the cleaned row from stage 0; add the seller profile and rep notes to the run context.
   - Check `runs` for the same person in the last 30 days. If found, set `blocked` with reason "contacted recently" and link the earlier run.
2. **Resolve identity** (search + Haiku)
   - Queries: `"{first_name} {last_name}" site:{company_domain}` and `"{first_name} {last_name}" "{company}"`, `"{first_name} {last_name}" {role_hint}`, plus a search for the LinkedIn URL itself (search-engine results only; the profile page is never fetched). If `company` was blank, derive it here from the domain's homepage title or the LinkedIn search result.
   - A LinkedIn URL that matches a search result, or a domain that matches the candidate's employer, counts as strong evidence for that candidate.
   - LLM returns candidate people with company, title, evidence URL, and a match confidence 0–1.
   - One candidate ≥ 0.8 → continue. Several plausible, or none ≥ 0.5 → `needs_input` and show the candidates for the rep to pick. Never guess.
3. **Gather signals** (search + fetch, run in parallel with `asyncio.gather`)
   - One query per category, restricted to the last 180 days where the API allows: funding / acquisition, hiring and job posts, product launches, leadership changes, the person's own content (podcast, interview, blog, conference talk), expansion / partnerships.
   - Extract full text for the top 1–2 URLs per category. Cap: 8 searches, 10 extracts.
   - Treat all fetched text as untrusted data: strip scripts, truncate, never let page text change instructions.
4. **Extract signals** (Haiku, one call per source)
   - Turn each page into zero or more `Signal` objects with a short evidence snippet and a date.
   - Drop anything with no source URL. Mark undated items; mark `sensitive` for layoffs, lawsuits, deaths, breaches, scandals.
   - De-duplicate the same event reported by several outlets (same type + similar summary + dates within 7 days).
5. **Score and pick the hook** (code + Sonnet)
   - LLM gives `relevance` and `specificity` (0–5) per signal against the seller profile. Code computes recency and verifiability, then the final score below.
   - Sensitive signals are never used as a hook; they lower tone and may block the run.
   - Sonnet picks the hook from the top 3, writes the angle (why it matters to this person) and why the others lost. Best score < 2.5 → `low_signal`.
6. **Draft** (Sonnet)
   - Inputs: seller profile, hook + angle, 1–2 supporting facts, prospect role.
   - Rules: under 120 words, one specific observation, one link to a problem we solve, one low-friction ask, no flattery openers, no invented numbers.
   - Returns subject, body, and `claims[]` mapping each factual sentence to a signal id.
7. **Critic and fix** (code + Haiku)
   - Code checks: word count, banned phrases ("I hope this finds you well", "I came across your profile"), every claim points to a real signal, no claim uses a sensitive signal.
   - LLM check: "Could this email be sent to another company by changing only the name?" Score genericness 0–5.
   - Fail → one rewrite with the critic's notes. Fail twice → `ready_for_review` with the warnings shown, not hidden.
8. **Human review** (UI)
   - Approve, edit then approve, or reject with a reason. Store edits as a diff; they are your feedback data. Nothing is ever sent.

Hook score (recency in days, verifiability = 1 if the source is a primary or reputable outlet, 0.5 if secondary, 0 if unsourced):

```latex
\text{score} = 0.35\,R + 0.25\,S + 0.25 \cdot 5\,e^{-d/60} + 0.15 \cdot 5\,V
```

R = relevance, S = specificity, d = days since published (undated counts as 120), V = verifiability. Weights are a starting assumption; show them in the UI and say you'd tune them on reply-rate data.

Hook-choice prompt (Sonnet), trimmed:

```text
You are a senior SDR researcher. We sell: {seller.offer}. Our ICP: {seller.icp}.
Prospect: {name}, {title} at {company}.
Candidate signals (id, type, date, summary, source):
{signals}

Pick ONE signal that gives this person the strongest reason to reply to us.
Prefer: about the person over the company, recent over old, tied to a problem we solve.
Never pick a signal marked sensitive.
If no signal links to what we sell, return signal_id = null.
Return the HookChoice JSON: signal_id, angle (1-2 sentences, why it matters to them),
runners_up, why_not_others.
```

Draft prompt (Sonnet), trimmed:

```text
Write a first-touch cold email from {rep.name} at {seller.company}.
Hook: {hook.summary} (source: {hook.url}). Angle: {angle}.
Rules: <120 words. Open with the specific observation, not a compliment.
One sentence connecting it to {seller.problem_solved}. One soft ask (15 min or a resource).
Only state facts present in the signals provided. No made-up metrics. Plain text, no emoji.
Return JSON: subject (<7 words, lowercase ok), body, claims[{sentence, signal_id}].
```

Engineering details that save you in the demo:

- Wrap every LLM and search call in retry with backoff (tenacity, 3 tries) and a timeout; on final failure, emit a `failed` stage event with the error in plain words.
- Log tokens and credits per stage into `stage_events.payload`; show total cost per run on the dashboard.
- Add `DEMO_MODE=fixtures` that replays saved search results from `tests/fixtures/` if the network or an API dies mid-interview.

## Edge cases: what to build

Demo four: **buying committee (3), crowded hook (5), stale draft (10) and AI screening (11)**. Together they show account-level thinking, signal judgement, safety, and what happens after generation. Build five more as cheap rules that show up in the run's reasoning, and keep the last three as interview talking points. The brief asks for 2–4, so twelve fully built cases would cost you polish on the four that matter.

| # | Case | Tier | Where it plugs in | Why this tier |
| --- | --- | --- | --- | --- |
| 3 | Buying committee in the same batch | **Demo** | New stage: account plan, after scoring | Most visual case; you control the CSV, so it's reliable live |
| 5 | Crowded hook | **Demo** | Score stage: crowdedness penalty | Shows signal judgement beyond "most recent wins" |
| 10 | Draft goes stale in review | **Demo** | New step: revalidate on approve | Happens after generation; few candidates think of it |
| 11 | Prospect screening for AI outreach | **Demo** | Extract stage: injection detector → signal | Safety plus a useful twist; easy to feed a test bio |
| 1 | Warm prospect treated as cold | Cheap rule | Stage 1: sequence history lookup | One table lookup switches message type or suppresses |
| 2 | Live deal collision | Cheap rule | Stage 1: open-opportunity lookup | One lookup; holds draft and routes to deal owner |
| 6 | Competitor signal disguised as a hook | Cheap rule | Score stage: competitor list check | A list in the seller profile + one prompt rule |
| 7 | Borrowed words (wrong speaker, reshares) | Cheap rule | Extract stage: `speaker` + `is_reshare` fields; critic check | Two schema fields and one critic rule |
| 9 | Template drift across the batch | Cheap rule | After drafting: batch similarity check | Natural partner to case 3; gives the dashboard a diversity score |
| 4 | Ghost job postings | Talk, light heuristic | Extract stage | Needs posting history the web rarely gives cleanly; mention the "hard to fill" reframe |
| 8 | Acquired or rebranded company | Talk | Resolve stage | Needs enrichment data you can't fake convincingly |
| 12 | Cross-border prospect | Talk, flag only | Resolve stage: country → `needs_input` for strict regions | Legal; the right build is "stop and flag", which is one rule |

The original four (ambiguous identity, thin signal, sensitive news, job change) stay as baseline behaviour in the pipeline. They no longer need demo time.

Two judgement calls to state in the interview: the relationship cases (1, 2, 3, 10) are the most reliable live because their data lives in your own tables, not on the web; and none of the cases lets the agent make a legal call or invent history, only stop, flag and show its reasons.

## The four demo cases in full

Each case below lists how it's detected, what the process does, the exact test input, and what the interviewer sees. Build them in this order; 3 and 9 share code, and 10 needs the claim-to-signal links from the draft stage.

### Case 3 — Buying committee in the same batch

- **Detect:** in the batch layer, group valid rows by normalised company domain. Rows with only a LinkedIn URL join an account once stage 2 resolves their company domain (fallback: normalised company name). Any group of 2+ people becomes an account.
- **Do:** run stages 1–5 per person, sharing one set of company research. Then a new **account plan** step (Sonnet) sees every member's title and the pooled signals, and assigns each person a different hook and angle using a persona map from the seller profile: CFO → cost of scaling, Controller → close timeline and accuracy, AP Manager → team workload.
- **Enforce in code, not the prompt:** no signal is used as an opener twice within an account; a company-wide event (the Series C) may appear as sentence-two context for at most one person. If the LLM's plan breaks a rule, reject it and retry once with the violation named.
- **Send plan:** recommend staggered send days (e.g. AP Manager day 0, Controller day 2, CFO day 5). State the order as an assumption in the seller profile.
- **Test input:** three Northwind rows in the demo CSV (CFO, Controller, AP Manager) with saved fixtures for Northwind's signals.
- **On screen:** an account card in the batch view with the three people side by side, each one's hook chip, a green "no reused openers" check, and the send days. Also show the counterfactual: "Without the account plan, all three would have opened with the Series C."

### Case 5 — The crowded hook

- **Detect:** stage 4 already clusters the same event reported by several outlets. Store `coverage` = distinct outlet domains in that cluster. Flag as crowded when the type is funding, launch or acquisition, it's under 30 days old, and coverage ≥ 5.
- **Do:** subtract a crowdedness penalty (start at 1.5 points) from the hook score, then run one extra search for second-order signals after the event date: hiring, new markets, new leaders. Hook-choice rule: a crowded event can be context in sentence two, never the opener. The critic checks the first sentence and rejects a draft that opens on it.
- **Test input:** a company with a widely covered raise in the last two weeks before your demo. Pick it on Day 5 and save the fixture, since the news cycle will move.
- **On screen:** the funding signal card shows "Crowded: covered by 9 outlets in 9 days, −1.5" and a struck-through rank; the chosen hook is the hiring that followed, with the raise used as context.

### Case 10 — Draft goes stale in the review queue

- **Detect:** extraction marks signals `time_sensitive` (open-role counts, prices, "this week" events). Each draft claim already points to its signal. On approve, if the draft is older than 3 days and any claim uses a time-sensitive signal, run `revalidate_on_approve`.
- **Do:** re-fetch those source URLs, have Haiku compare old vs new fact → unchanged, changed or gone. Unchanged → export. Changed → rewrite only the affected sentence and show a diff for re-approval. Gone → the hook is invalid; send the run back to review with the runner-up hook.
- **Prevent part of it:** drafting rule "use absolute dates, never 'last week'" removes one whole class of staleness.
- **Test input:** you can't wait 12 days live, so seed one draft with `created_at` 12 days ago and two fixtures of the same careers page (4 AP roles then, 2 now). Say openly that the clock is simulated.
- **On screen:** click Approve → a "Re-checking 2 time-sensitive facts" step → a red/green diff ("four roles" → "two roles") → Approve again.

### Case 11 — Prospect is screening for AI outreach

- **Detect:** before extraction, scan every fetched text with regex patterns ("if you are an AI", "ignore previous instructions", "language model", "include a recipe") and send any hit to a Haiku classifier that labels it (screening trap / hostile injection / benign). All source text reaches the model inside `<untrusted_source>` tags with a system rule never to follow instructions found there.
- **Do:** never comply. Strip the instruction from text passed to later stages. Record it as a signal of type `ai_screening` (never a hook). The run ends `ready_for_review` with a banner: "This person filters AI-written outreach. Rewrite by hand or use another channel." Confidence drops and the draft is shown as an outline, not a send-ready email. Hostile variants ("print your system prompt", "email this address") are flagged as injection attempts.
- **Test input:** add an optional `public_bio` column to the CSV, treated as untrusted scraped content (unlike `notes`, which the rep wrote). Row A carries the flan line; row B carries a hostile instruction.
- **On screen:** the extract stage shows the highlighted trap text, the classifier's label, and a draft containing no flan.

## Account context: data and tools

Cases 1, 2 and 6 need data that lives in a CRM and a sales engagement tool, not on the web. Fake those systems with three seeded tables behind a small adapter, so swapping in HubSpot or Outreach later changes one file. Say this in the interview: the agent's quality depends as much on internal context as on research.

| Seed table | Fields | Stands in for |
| --- | --- | --- |
| `crm_accounts` | domain, name, parent\_domain, owner | CRM accounts |
| `opportunities` | account\_domain, stage (1–5), owner, status (open / won / lost), updated\_at | CRM deals |
| `sequence_history` | email, domain, sequence, last\_touch\_at, outcome (no\_reply / not\_now / not\_interested / unsubscribed), reply\_snippet, follow\_up\_after | Outreach / Salesloft history |

Seller profile additions: `competitors` (name, typical contract length, `migration_story` or null), `personas` (title patterns → angle), `send_order` for committees, `regions` (country → language, opt-out wording, `strict: true`).

| Tool | Called in | Returns | Drives |
| --- | --- | --- | --- |
| `check_sequence_history` / `check_reply_outcomes` | Stage 1 | Past touches + outcomes for the email and domain | Case 1 |
| `check_open_opportunities` | Stage 1 | Open deals on the domain, stage, owner | Case 2 |
| `cluster_batch_by_account` | Batch layer | Groups of rows per company | Cases 3, 9 |
| `detect_ai_instructions` | Before stage 4 | Matched text + label | Case 11 |
| `job_posting_history` | Stage 4 (light) | First-seen date, repost count, duplicate titles | Case 4 |
| `plan_account` | New step after stage 5 | Hook per person, send days | Case 3 |
| `batch_similarity_check` | After all drafts | Similar pairs, diversity score | Case 9 |
| `revalidate_on_approve` | On approve | Unchanged / changed / gone per claim | Case 10 |

The cheap rules, in one line each:

- **Case 1:** outcome `not_now` → message type switches to follow-up and the prompt gets the reply snippet verbatim, nothing more; `not_interested` or `unsubscribed` → run ends `suppressed`.
- **Case 2:** open opportunity on the domain → run ends `held`, reason "Open opportunity with Sam, stage 4. Draft held", draft routed to the owner; optional AE brief instead of an email.
- **Case 6:** a signal naming a listed competitor → bought in the last 12 months: `held` with a renewal reminder date; established user and a `migration_story` exists: displacement angle; otherwise `held`.
- **Case 7:** extraction returns `speaker` and `is_reshare`; a person-level signal counts only if the speaker is the prospect; otherwise reattribute ("Your CFO said on…") or drop. The critic rejects any quote not tied to the prospect as speaker.
- **Case 9:** TF-IDF cosine (scikit-learn) across all draft bodies in the batch; pairs above 0.8 are flagged; no single hook source may open more than 20% of drafts, and overflow prospects are re-scored with that source excluded. Diversity score = 1 − mean pairwise similarity, shown on the batch view.

These add two statuses to the run status list: `suppressed` (prospect opted out or said not interested) and `held` (open deal, recent competitor purchase, or a strict-region compliance flag), each with its reason shown.

Updated pipeline order: batch layer (cluster accounts) → 1 intake + account context → 2 resolve → 3 gather → AI-instruction scan → 4 extract + verify → 5 score (crowdedness, competitors) → account plan → 6 draft → 7 critic → batch similarity → 8 review → revalidate on approve.

## UI: three screens

The brief says the UI is graded and must have a live run view and a dashboard. Three screens cover it; spend real time on the run view, it's what they'll watch.

**1. Upload and preview (`/batches/new`)**

- Drag-and-drop CSV upload, plus a "Use demo CSV" button and a downloadable template.
- Preview table of the first 20 rows, detected column mapping with dropdowns to correct it, and counts: valid, invalid (with reasons), duplicates.
- "Run N prospects" button. Seller profile shown read-only at the side (what we sell, ICP, tone), editable in settings.

**1b. Batch view (`/batches/[id]`)**

- Progress bar (12 of 20 done) and live status counts from `batch_progress` events.
- One row per prospect: name, company, current stage (live), status chip, hook type, confidence. Click a row to open its run view.
- "Review queue" button that steps through every `ready_for_review` draft in order, with keyboard shortcuts for approve / reject / next.
- "Export CSV": the original columns plus status, hook, source URL, subject, body (approved rows only by default).

**2. Live run view + review (`/runs/[id]`)**

- Left column: a vertical stepper of the 8 stages. Each shows a spinner, then a tick, warning or cross, its duration, and a one-line result ("Found 1 match, confidence 0.92").
- Click a stage to expand: queries run, sources fetched, raw LLM output, credits used.
- Middle: signal cards as they arrive, each with type chip, date, source link and score bar. The chosen hook gets a highlighted border; runners-up show "why not".
- Right (once drafting ends): the draft in an editable box, each factual sentence underlined and linked to its source on hover, critic results as a checklist, and Approve / Edit / Reject buttons.
- `needs_input` states show inline: candidate cards with "This is the one" buttons that resume the run.
- Implementation: `EventSource('/api/runs/{id}/events')`; each event is `{stage, state, message, payload}`; reduce events into page state. On reload, fetch stored events first, then subscribe.

**3. Dashboard (`/`)**

- Metric cards: batches and prospects this week, % ready for review, approval rate, median run time, average credits per run.
- Batches table: file name, uploaded, rows, progress, ready / needs input / blocked counts; click opens the batch. A second tab lists all runs across batches with status filter and name search.
- Small "why not ready" breakdown: counts of `needs_input`, `low_signal`, `blocked`, `failed`. Shows the process is honest about its failures.

Style: one accent colour, status colours consistent everywhere (green ready/approved, amber needs input / low signal, red blocked/failed, grey running), plenty of white space, no animations beyond the stepper.

## Day-by-day plan

Happy path end-to-end by the end of Day 3, edge cases on Days 4–5, polish and deploy on Day 6, record on Day 7. If you slip, cut UI polish before cutting edge cases.

**Day 1 — Pick and map (half day)**

- [ ] Email the coordinator: "ASA Case Study — Shivam — PS-3"
- [ ] Draw the process map on paper (stages, decisions, statuses)
- [ ] Write `seller_profile.yaml`: pick a believable seller (e.g. an AP automation tool selling to finance leaders) so relevance scoring means something
- [ ] Get API keys: Anthropic, Tavily, Exa; start `ASSUMPTIONS.md`

**Day 2 — Backend skeleton + research**

- [ ] FastAPI app, SQLModel tables (incl. batches), `POST /batches, POST /batches/{id}/start, GET /batches/{id}`, `GET /runs`, `GET /runs/{id}`, SSE endpoint
- [ ] `runner.py` that executes stages in order and writes `stage_events`
- [ ] `llm.py` (JSON-schema tool output, retries) and `search.py` (Tavily + cache)
- [ ] CSV parser + validator (header aliases, dedupe, row errors) with unit tests; stages 1–3 working from a CLI script on a 5-row CSV

**Day 3 — Happy path end-to-end**

- [ ] Stages 4–7: extract, score, hook choice, draft, critic
- [ ] Batch runner with semaphore + company cache; Next.js upload + preview, batch view and bare run view on SSE
- [ ] One full CSV from upload to drafts in the browser. Commit and tag `v0-happy-path`

**Day 4 — Account context + buying committee**

- [ ] Seed tables + adapter; rules 1 (sequence history) and 2 (open deals)
- [ ] `low_signal` path; case 3 account plan + case 9 batch similarity
- [ ] Save Northwind fixtures for the committee demo

**Day 5 — Signal judgement, safety, staleness + review**

- [ ] Case 5 crowdedness penalty + second-order search; case 11 AI-instruction scan; rules 6 and 7
- [ ] Case 10 revalidate on approve, demoed with a seeded 12-day-old draft
- [ ] Review queue: claim highlighting, approve / edit / reject, edit diffs saved, export CSV

**Day 6 — Dashboard, polish, deploy**

- [ ] Dashboard metrics + runs table + filters
- [ ] Visual pass: status colours, empty states, error states
- [ ] Deploy backend (Render/Railway) and frontend (Vercel), Postgres, env vars
- [ ] Run all 5 demo cases on the deployed URL twice; fix what breaks

**Day 7 — Record and submit**

- [ ] Rehearse the video twice, then record (script below)
- [ ] Final check of the live link in a private browser window
- [ ] Send both links to the coordinator

## Testing, demo prep and deploy

The live demo is where submissions are won or lost, so treat reliability as a feature.

**Tests worth writing (pytest, \~1 hour total)**

- Scoring: given fixed signals, the expected hook wins; a sensitive signal never wins.
- Critic: catches over-length drafts, banned phrases, and a claim with no signal id.
- Status routing: each fixture case ends in its expected status.
- Run each fixture end-to-end with `DEMO_MODE=fixtures` in CI or a pre-demo script.

**Quality check on drafts** — run 10 real prospects, read every draft, and score each yourself 1–5 on "would I reply". Note the average in `ASSUMPTIONS.md`; it's a strong interview line ("7 of 10 were sendable with minor edits; the 3 misses were all thin-signal companies").

**5-minute video script**

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:30 | Dashboard | The problem in one line, what the tool does, that nothing sends without a human |
| 0:30–2:15 | Happy path live | Upload the demo CSV, show the preview catching the bad and duplicate rows, then open the happy-path row: each stage as it runs; why identity is checked first; why this hook beat the runners-up; the cited draft |
| 2:15–3:15 | Case 3 (buying committee) | The account card: three people, three different hooks, no reused opener, staggered send days |
| 3:15–4:15 | Cases 11 and 10 (AI screening, stale draft) | The trap text it refused to follow; then approve a 12-day-old draft and watch it re-check and show the diff |
| 4:15–5:00 | Dashboard + architecture in one breath | Batch results side by side, approve one draft, export CSV, cost per prospect, what you'd build next |

**Before the live interview**

- [ ] Demo CSV ready (happy path + 4 edge cases + messy rows) and a second clean CSV as backup; order to open rows written down: happy path (the crowded-hook company) → buying committee → AI screening → stale draft → warm prospect / open deal if asked
- [ ] Ran all cases on the deployed URL within the last 24 hours
- [ ] Backend warmed up 10 minutes before (free hosts sleep and cold-start slowly)
- [ ] `DEMO_MODE=fixtures` toggle ready if the network or an API fails; say so openly if you use it
- [ ] API credit balances checked

**Deploy checklist**

- [ ] Secrets only in host env vars; `.env` in `.gitignore`
- [ ] CORS limited to the Vercel domain
- [ ] Simple rate limit on `POST /batches` (e.g. 5 uploads per hour per IP, max 50 rows per file) so a shared link can't drain credits
- [ ] Health endpoint and a friendly error page
- [ ] README with a one-paragraph overview, run-locally steps, and the process map image

## Assumptions and interview talking points

The FAQ asks you to make assumptions and note them. Keep this list in `ASSUMPTIONS.md` and add to it as you build.

**Assumptions to start with**

- The seller is fictional but specific (one product, one ICP, one problem solved); relevance scoring depends on it.
- Input is a CSV of up to 50 prospects per upload; larger files and CRM import are next steps.
- Output is a first-touch email only; no LinkedIn messages, no follow-up sequences.
- Signals older than 180 days are ignored; weights in the hook score are hand-set.
- Public web only. No LinkedIn scraping; rep-pasted notes are trusted as rep input.
- "Recently contacted" is checked against this tool's own history, standing in for a CRM.

**Questions to expect, with your answer in one line**

| Likely question | Your answer |
| --- | --- |
| Why not one big agent with web search? | Fixed stages are predictable, cheaper, and every step is inspectable; the LLM judges inside a stage, code owns the flow |
| How do you stop hallucinated facts? | Every factual sentence must map to a fetched signal; the critic rejects any that doesn't |
| Why two models? | Haiku for many small extraction calls, Sonnet where writing quality matters; roughly cost × quality |
| What happens with 200 prospects? | Already batch-shaped: raise the row cap, move runs to a proper job queue, keep the per-company cache and the review queue |
| How would you know it works? | Track approve rate, edit distance between draft and sent, and reply rate; tune hook weights on replies |
| What would you build next? | CRM integration (HubSpot/Salesforce) for history and sending, follow-up drafts, per-rep tone learned from their edits |

**The one-sentence pitch for a non-technical buyer:** "Your rep uploads their prospect list; a few minutes later each person has a short email built on one real, cited fact about that person, and the tool tells them honestly when it couldn't find one."

## Build tasks for Claude

Eighteen tasks, each sized for one Claude Code session and each ending in something you can run. Work top to bottom; a task starts only when the tasks it depends on are Done. Copy each prompt as written; it points Claude at the matching section of this guide, so the guide itself becomes the spec.

**Set-up, once**

1. Export this doc as Markdown and save it in the repo as `docs/GUIDE.md`.
2. Open the repo folder in Claude Code (terminal, desktop app, or this app with the folder connected).
3. Run task T01; it writes `CLAUDE.md`, which Claude reads at the start of every later session.
4. Put API keys in `backend/.env` yourself (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`). Never paste keys into a prompt.

**Working rhythm, every task**

- One task per session. Start a fresh session for the next one so context stays clean.
- For tasks marked *plan first*, say "show me the plan before writing code" and approve it before Claude starts.
- Claude runs the task's checks and shows you the output. You read it, run it yourself once, then commit: `git commit -m "T05 runner + SSE"`.
- If a task drifts after two correction attempts, `git reset --hard` to the last commit and re-prompt with what went wrong. Cheaper than patching a tangle.
- Use `DEMO_MODE=fixtures` from T03 onwards for repeated runs, so testing doesn't burn search credits.

| ID | Task | Day | Depends on | Status |
| --- | --- | --- | --- | --- |
| T01 | Repo scaffold + `CLAUDE.md` | 1 | — | Not started |
| T02 | Database models + seed tables | 2 | T01 | Not started |
| T03 | LLM and search adapters, fixture mode | 2 | T01 | Not started |
| T04 | CSV ingest + preview API | 2 | T02 | Not started |
| T05 | Batch runner, account clustering, SSE (*plan first*) | 2 | T02, T03 | Not started |
| T06 | Stages 1–3: intake, account context, resolve, gather | 3 | T05 | Not started |
| T07 | Stage 4: AI-instruction scan + extraction | 3 | T06 | Not started |
| T08 | Stage 5: scoring + hook choice | 3 | T07 | Not started |
| T09 | Stages 6–7: draft + critic | 3 | T08 | Not started |
| T10 | Frontend scaffold + upload and preview | 3 | T04 | Not started |
| T11 | Batch view + live run view | 3 | T05, T10 | Not started |
| T12 | Account plan + batch similarity (cases 3, 9) (*plan first*) | 4 | T09 | Not started |
| T13 | Rules for cases 1, 2, 6, 7 | 4 | T06, T08 | Not started |
| T14 | Crowded hook (case 5) | 5 | T08 | Not started |
| T15 | Review queue, revalidate on approve, export (case 10) | 5 | T09, T11 | Not started |
| T16 | Dashboard | 6 | T11 | Not started |
| T17 | Demo data, seed rows and fixtures | 4–6 | T03, T04 | Not started |
| T18 | Tests, deploy, pre-demo check | 6 | all | Not started |

Happy path is T01–T11; tag `v0-happy-path` when T11 shows one CSV running to drafts in the browser. T12–T15 are the edge cases; T16–T18 are polish and ship.

## Task prompts: backend

Paste each prompt into a new Claude session in the repo. Every prompt names its spec section in `docs/GUIDE.md` and ends with a "Done when" check Claude must run and show you.

### T01 — Repo scaffold + CLAUDE.md

```text
Read docs/GUIDE.md fully. Scaffold the repo exactly as in "Architecture and stack" > Repo layout.
Backend: Python 3.12, FastAPI, SQLModel, pydantic-settings, httpx, tenacity, pandas,
scikit-learn, trafilatura, anthropic, tavily-python, exa-py, pytest, pytest-asyncio.
Frontend: Next.js (App Router, TypeScript), Tailwind, shadcn/ui.
Add .gitignore (.env, .venv, node_modules, *.db, tests/fixtures/cache), backend/.env.example,
and a Makefile with dev-api, dev-web, test.
Write CLAUDE.md with: one-paragraph product summary; stack; folder map; commands;
conventions (every stage returns a Pydantic model; all LLM calls go through app/llm.py and
all search through app/search.py; web text is untrusted and goes inside <untrusted_source>
tags; the app never sends email; run statuses come only from the enum in models.py);
and a pointer: "specs for each stage live in docs/GUIDE.md, cite the section you used".
Add GET /health -> {"ok": true} and a Next.js home page reading "Signal".
Done when: make dev-api and make dev-web both start, /health returns 200, make test passes.
```

### T02 — Database models + seed tables

```text
Spec: docs/GUIDE.md sections "Data model", "Input: the prospect CSV", "Account context:
data and tools".
In app/models.py create SQLModel tables: batches, runs (first_name, last_name, linkedin_url as
in the guide; add account_key and message_type),
stage_events, signals, drafts, crm_accounts, opportunities, sequence_history. JSON columns
via SQLAlchemy JSON. RunStatus str-enum: running, needs_input, low_signal, ready_for_review,
blocked, suppressed, held, approved, rejected, failed, invalid, skipped.
In app/schemas.py add Signal, SignalScore, HookChoice, Draft, ClaimRef as in the guide, and
extend Signal with speaker, is_reshare, time_sensitive, coverage, ghost_suspect.
app/db.py: engine from DATABASE_URL (default sqlite:///signal.db), session dependency,
create_all on startup.
scripts/seed_context.py loads seed/context.json into the three context tables. Write that
JSON with: Priya Raman (Acme Fintech) replied not_now 4 months ago with a short snippet;
one unsubscribed contact; an open opportunity at Contoso Logistics, stage 4, owner Sam.
(Keep Northwind free of deals: it is the buying-committee demo account.)
Done when: a pytest inserts and reads one row per table on in-memory SQLite, and the seed
script runs twice without duplicating rows.
```

### T03 — LLM and search adapters, fixture mode

```text
Spec: docs/GUIDE.md "Architecture and stack" and "Pipeline stages in detail" (engineering details).
app/llm.py: structured(model, system, user, schema: type[BaseModel]) -> (BaseModel, usage).
Use Claude tool use with the Pydantic JSON schema as the only tool and tool_choice forcing it.
Models from settings: SONNET=claude-sonnet-5-5, HAIKU=claude-haiku-5-5. tenacity retry
(3 tries, exponential backoff) on rate-limit/5xx, 60 s timeout, validation error -> one retry
with the error text appended.
app/search.py: SearchProvider protocol with search(query, depth, days) and extract(urls).
TavilyProvider first, ExaProvider as fallback when Tavily errors or returns nothing.
Return credits used per call. 24 h disk cache keyed by provider+query+params.
DEMO_MODE env: "live" (default), "record" (live + save responses to tests/fixtures/<slug>.json),
"fixtures" (read only from fixtures; raise FixtureMissing naming the slug).
app/fetch.py: httpx GET + trafilatura text, 10 s timeout, 20k-char cap.
Done when: scripts/smoke.py runs one search and one structured call and prints results,
tokens and credits; unit tests (mocked) cover retry, fallback and fixture mode.
```

### T04 — CSV ingest + preview API

```text
Spec: docs/GUIDE.md "Input: the prospect CSV". Also accept an optional public_bio column
(untrusted, like scraped web text) and keep it separate from notes (trusted rep input).
app/ingest.py: read with pandas (dtype=str, keep_default_na=False, encoding utf-8-sig),
map headers by the alias table, clean values, run file-level checks (cap 50 rows) and
row-level checks (invalid with reason, skipped duplicate within file). Store raw_row.
Endpoints: POST /batches (multipart file) -> batch_id + preview {mapping, counts, first 20 rows
with status and reason}; PATCH /batches/{id}/mapping -> re-validate with a corrected mapping.
Required per row: first_name, last_name, and at least one of company_domain / linkedin_url
(company name is recommended, not required). Split a single full_name column into first + last.
Validate domain and LinkedIn URL formats; dedupe on LinkedIn URL, email, or first + last + domain.
Done when: pytest covers clean file, aliased headers, BOM, full_name split, missing last name,
missing both domain and LinkedIn URL, LinkedIn-only row (valid), malformed LinkedIn URL,
in-file duplicate, 51 rows, and a non-CSV file, each with the expected statuses and messages.
```

### T05 — Batch runner, account clustering, SSE (plan first)

```text
Spec: docs/GUIDE.md "Pipeline stages in detail" (batch layer) and "UI: three screens" (SSE).
Show me a plan before writing code.
app/pipeline/runner.py: a stage is `async def run(ctx) -> BaseModel`. A wrapper emits
started/done/failed events (stage, state, message, payload, duration_ms, tokens, credits)
into stage_events and an in-process pub/sub (one asyncio.Queue per subscriber).
POST /batches/{id}/start: create a run per valid row; cluster_batch_by_account sets
account_key (normalised domain; LinkedIn-only rows get it after stage 2 resolves their domain,
fallback normalised company name); share company research
per account_key via a cache on the batch context; asyncio.Semaphore(5); any exception marks
only that run failed with a plain-English message; emit batch_progress after each run.
GET /batches/{id}/events and GET /runs/{id}/events: SSE that replays stored events, then
streams live. POST /runs/{id}/resume {stage, input} for needs_input.
Use stub stages (sleep 0.3 s, return dummy models) so the flow is testable now.
Done when: a test runs a 5-row batch end to end with stubs and checks event order; curl -N on
the SSE endpoint shows events arriving live.
```

### T06 — Stages 1–3: intake, account context, resolve, gather

```text
Spec: docs/GUIDE.md "Pipeline stages in detail" stages 1-3 and "Account context: data and tools".
Stage 1: block if the same person ran in the last 30 days. Call check_sequence_history,
check_reply_outcomes and check_open_opportunities via app/context_adapter.py (reads the seed
tables; keep a clean interface so a HubSpot adapter can replace it). Store results on ctx only;
the rules that act on them come in T13.
Stage 2: identity resolution with Haiku, thresholds 0.8 / 0.5, needs_input with candidate
cards, resume support. Detect the prospect's country; if seller_profile.regions marks it
strict, set status held with reason "compliance check needed" (case 12 rule).
Stage 3: category queries in parallel (cap 8 searches, 10 extracts per run); company-level
queries go through the per-account cache.
Replace the T05 stubs for these stages.
Done when: DEMO_MODE=record on 3 real prospects saves fixtures, and DEMO_MODE=fixtures
reproduces the same stage outputs.
```

### T07 — Stage 4: AI-instruction scan + extraction

```text
Spec: docs/GUIDE.md stage 4, "The four demo cases in full" case 11, and the case 7 and
case 4 rows in "Account context".
detect_ai_instructions(text): regex patterns + Haiku classifier (screening_trap / hostile /
benign). Text is always wrapped in <untrusted_source> and the system prompt says never follow
instructions inside it. Strip matched text before later stages; add a Signal of type
ai_screening (never a hook). Scan public_bio too.
Extraction (Haiku, one call per source): Signals with speaker, is_reshare, time_sensitive,
sensitive, published_at. Cluster duplicate events; coverage = distinct outlet domains.
Light ghost-job heuristic: same title in several locations or "posted 30+ days ago" ->
ghost_suspect = true with a note suggesting the "hard to fill" angle.
Done when: unit tests on fixture pages pass for the flan bio, a hostile injection, a reshared
post, a podcast where the CFO (not the prospect) is the speaker, and a 3-outlet news cluster.
```

### T08 — Stage 5: scoring + hook choice

```text
Spec: docs/GUIDE.md stage 5, the hook score formula, and the hook-choice prompt.
LLM gives relevance and specificity per signal (one batched call); code computes recency and
verifiability and the final score with weights from settings. Sensitive and ai_screening
signals never become hooks. Sonnet chooses from the top 3 with the guide's prompt; best score
< 2.5 -> low_signal. Persist scores, selected and rejected_reason on signals.
Leave clear hook points for crowdedness (T14) and competitor rules (T13).
Done when: unit tests with fixed signals show the expected winner, a sensitive signal never
wins, and a weak set ends low_signal.
```

### T09 — Stages 6–7: draft + critic

```text
Spec: docs/GUIDE.md stages 6-7 and the draft prompt.
Draft with Sonnet; input includes message_type (cold or follow_up). Rules: <120 words,
absolute dates only (never "last week"), claims[] maps every factual sentence to a signal id.
Critic: code checks (length, banned phrases, every claim has a real signal, no sensitive
signal, quotes only where speaker == prospect) + Haiku genericness score. One rewrite with
the critic notes; a second failure still goes to ready_for_review with warnings stored.
Done when: critic unit tests pass and one fixture prospect runs from CSV row to a stored
ready_for_review draft.
```

### T12 — Account plan + batch similarity, cases 3 and 9 (plan first)

```text
Spec: docs/GUIDE.md case 3 in "The four demo cases in full" and case 9 in "Account context".
Show me a plan before writing code.
plan_account: for an account_key with 2+ runs, wait until all members finish stage 5
(asyncio barrier per account), then Sonnet assigns one hook + angle per person using
seller_profile.personas. Enforce in code: no opener signal reused within the account; a
company-wide event used as context for at most one person. Retry once naming the violation.
Store send_day per run from seller_profile.send_order. Store the counterfactual (hook each
person would have got alone).
batch_similarity_check after all drafts: TF-IDF cosine over bodies, flag pairs > 0.8, cap any
single hook source at 20% of drafts and re-score overflow runs with it excluded, store
diversity score on the batch.
Done when: the 3 Northwind rows get three different openers; a synthetic 10-draft test trips
the cap and recovers.
```

### T13 — Rules for cases 1, 2, 6, 7

```text
Spec: docs/GUIDE.md "Account context" > the cheap rules.
Case 1: not_now -> message_type follow_up, reply snippet passed verbatim, nothing invented;
not_interested / unsubscribed -> suppressed. Case 2: open opportunity -> held with reason
naming owner and stage; optional AE brief (short Sonnet summary) stored instead of a draft.
Case 6: signal mentions a seller_profile competitor -> recent purchase: held + renewal
reminder date; established user with migration_story: displacement angle; else held.
Case 7: person-level signals count only when speaker == prospect; otherwise reattribute to
the company or drop.
Done when: one test per rule passes against the seed data.
```

### T14 — Crowded hook, case 5

```text
Spec: docs/GUIDE.md case 5 in "The four demo cases in full".
Crowded = type in {funding, product_launch, acquisition}, age < 30 days, coverage >= 5.
Subtract the crowd penalty (settings, default 1.5); run one extra search for second-order
signals after the event date; hook-choice rule: crowded events never open; critic rejects a
first sentence that references one. Expose crowded, coverage and penalty in the signal payload
for the UI.
Done when: on the crowded-company fixture the chosen hook is not the funding round and the
funding appears at most as sentence-two context.
```

## Task prompts: frontend, demo data and ship

Frontend tasks can run in a second Claude session alongside backend work once T04 and T05 are in, since they only depend on the API shapes.

### T10 — Frontend scaffold + upload and preview

```text
Spec: docs/GUIDE.md "UI: three screens" > 1 Upload and preview, and the Style line.
Set up an API client (lib/api.ts) with NEXT_PUBLIC_API_URL, shared TypeScript types that
mirror the backend schemas, and a layout with a top nav (Dashboard, New batch).
Build /batches/new: drag-and-drop CSV upload, "Use demo CSV" button, template download,
preview table (first 20 rows with row status and reason), detected mapping with dropdowns
that call PATCH /batches/{id}/mapping, counts (valid / invalid / duplicate), and a
"Run N prospects" button that calls start and routes to /batches/{id}.
Status colours exactly as the guide's Style line, defined once in lib/status.ts.
Done when: uploading the demo CSV shows the right counts and mapping, fixing a mapping
re-validates, and Run starts a batch.
```

### T11 — Batch view + live run view

```text
Spec: docs/GUIDE.md "UI: three screens" > 1b Batch view and 2 Live run view.
/batches/[id]: progress bar and status counts from batch_progress SSE; one row per prospect
with live current stage, status chip, hook type, confidence; account cards grouping runs that
share account_key (send days and opener chips once T12 lands).
/runs/[id]: stage stepper (spinner -> tick / warning / cross, duration, one-line result,
expand for queries, sources, raw LLM output, credits); signal cards streaming in with type,
date, source link, score bar, crowded badge, "why not" for runners-up; needs_input candidate
cards that call resume; draft panel with each claim underlined and linked to its source.
SSE via a useEventStream hook: fetch stored events first, then subscribe; reconnect on drop.
Done when: a fixture batch run shows every stage animating live in both views, and a page
reload mid-run restores the same state.
```

### T15 — Review queue, revalidate on approve, export (case 10)

```text
Spec: docs/GUIDE.md case 10 in "The four demo cases in full" and "UI: three screens" review
queue items.
Backend: POST /runs/{id}/review {action: approve|edit|reject, body?, reason?}. On approve, if
the draft is older than 3 days (setting) and any claim uses a time_sensitive signal, run
revalidate_on_approve: re-fetch those URLs, Haiku compares old vs new -> unchanged / changed /
gone; changed -> rewrite only that sentence and return a diff needing re-approval; gone ->
back to review with the runner-up hook. Store edit diffs. GET /batches/{id}/export.csv =
original columns + status, hook, source_url, subject, body (approved only unless all=true).
Add a dev-only endpoint POST /dev/age-draft/{id}?days=12 behind DEMO_MODE != live.
Frontend: review queue page stepping through ready_for_review drafts with keyboard
shortcuts (a approve, e edit, r reject, j next); a "Re-checking N facts" step and a red/green
diff modal; an Export CSV button on the batch view.
Done when: an aged draft with the 4-roles -> 2-roles fixture shows the diff and exports the
corrected text.
```

### T16 — Dashboard

```text
Spec: docs/GUIDE.md "UI: three screens" > 3 Dashboard.
Backend: GET /stats (batches and prospects this week, % ready for review, approval rate,
median run time, avg credits per prospect, counts of needs_input / low_signal / blocked /
suppressed / held / failed) and paginated GET /batches, GET /runs with status filter and name
search.
Frontend /: metric cards, batches table (file, uploaded, rows, progress, outcome counts,
diversity score), an All runs tab with filters, and the "why not ready" breakdown. Empty and
error states for every panel.
Done when: after two fixture batches every number on the page matches a SQL count you run by
hand.
```

### T17 — Demo data, seed rows and fixtures

```text
Spec: docs/GUIDE.md "Input: the prospect CSV" sample, "Edge cases: what to build", and
"The four demo cases in full".
Create seed/demo.csv with: one happy-path row at the crowded-hook company; Northwind CFO,
Controller and AP Manager; one row whose public_bio holds the flan line and one holding a
hostile instruction; Priya Raman (warm, not_now); a Contoso Logistics contact (open deal);
plus messy rows (aliased headers in a second file seed/demo_messy.csv, a row with neither
company domain nor LinkedIn URL, a row missing a last name, a LinkedIn-only row, an exact duplicate). Write seed/demo_cases.json describing each row's expected status.
Record fixtures for every row with DEMO_MODE=record, plus the two careers-page snapshots
(4 roles, then 2) for case 10. Add scripts/check_demo.py that runs both CSVs in fixtures
mode and asserts each row's status against demo_cases.json.
Done when: check_demo.py passes twice in a row with the network off.
```

### T18 — Tests, deploy, pre-demo check

```text
Spec: docs/GUIDE.md "Testing, demo prep and deploy".
Run the full test suite and fix failures. Deploy: backend to Render (or Railway) with
Postgres (Neon) via DATABASE_URL, frontend to Vercel with NEXT_PUBLIC_API_URL; CORS limited
to the Vercel domain; rate limit POST /batches (5 per hour per IP, 50 rows max); friendly
error page; GET /health. Write README.md: one-paragraph overview, process map, run locally,
demo order, assumptions link.
Add scripts/predemo.sh: hits /health, runs check_demo.py against the deployed API in fixtures
mode, prints credit balances if the providers expose them, and warms the backend.
Done when: predemo.sh passes against the deployed URLs from a fresh terminal.
```

After T18, record the video using the script in "Testing, demo prep and deploy" and tick the Day 7 boxes.
