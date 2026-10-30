# Assumptions

Decisions and assumptions made while building Signal. Add to this as you build
(see docs/PROJECT_SPEC.md, "Assumptions and interview talking points").

## Product
- The seller is fictional but specific: Halyard, an outbound pipeline engine sold to growth,
  marketing and sales leaders at B2B SaaS companies (`seller_profile.yaml`). It replaced an
  AP-automation seller (Ledgerline) on 2026-10-19 because the real prospect list we demo
  with is Heads of Growth and CMOs; against an AP seller most rows correctly scored
  low_signal. Relevance scoring, hiring searches and personas all read this file.
- Input is a CSV of up to 50 prospects per upload; larger files and CRM import are next steps.
- Each prospect needs first name, last name and at least one anchor: their LinkedIn profile URL
  (linkedin.com/in/...), a work email, or the company domain. A personal email (Gmail, Yahoo,
  Outlook, iCloud, Proton...: settings.free_email_domains) is not an anchor, because it says
  nothing about the employer. A work email fills in a missing company domain. A LinkedIn URL is
  the strongest anchor (two Priya Sharmas at HDFC Bank share a domain), but it is not required:
  many exports lack it, and stage 2 asks the rep when name + domain finds more than one person.
  A LinkedIn URL that isn't a /in/ profile makes the row invalid only when it is the only anchor;
  otherwise it is dropped with a warning. The URL is only matched in search results, never fetched.
- Upload warnings (row stays valid, shown per row in the mapping dialog): the LinkedIn slug
  contains neither the first nor the last name (slugs like 'jdoe' or 'priya-s-1a2b' pass); the
  work email's domain differs from the company domain (a subdomain counts as the same).
- CSV intake (T04): files must be UTF-8 (BOM fine), comma/semicolon/tab delimited, at most
  1 MB and 50 rows. Row numbers in messages are spreadsheet line numbers (header = line 1).
  A malformed email is dropped with a warning rather than rejecting the row; a missing
  company name is only a warning. Duplicates keep the first valid copy.
- Tested on a real 100-row Saleshandy export: placeholder cells (" - ", "n/a") count as
  empty; link/email hosts (bit.ly, linktr.ee, gmail.com...) are never a company domain;
  initial-only last names are flagged; the fullest name ("Philipp Wenger Lebron", not just
  first + last) is kept as full_name for identity search; ", MBA"-style suffixes are dropped.
- Batch runner (T05): at most 5 runs at once across all batches (one global cap, to stay in
  free-tier rate limits). Only valid rows become runs; invalid and duplicate rows stay in
  the batch preview. Runs left 'running' by a server restart are marked failed at startup.
  Background work runs in-process (asyncio), so a restart loses in-flight runs by design.
- Stages 1-3 (T06): "researched recently" means the same person (LinkedIn URL, email, or
  name + domain) in another batch within 30 days, failed runs excluded. Identity: the
  model's confidence is checked by code (evidence URL must be a real search result and
  must name the person and company, else capped at 0.5 / 0.75); accept only >= 0.8 with no
  rival >= 0.5. Gather: 5 company + 2 person searches; a company source must name the
  company in its title or URL, a person source must contain the full name and the company;
  page text is cut to 6,000 characters before extraction (free-tier token limits).
- Stage 4 (T07): instructions aimed at AI tools are found by patterns that address the
  reader ("if you are an AI...", "ignore previous instructions", "email this to x@y");
  a bare "language model" is not a hit, because it fires on every AI company's blog. Hits
  are labelled by the fast model; if it is down, they count as hostile. A signal survives
  only if 80% of its evidence words are on the page and it is under 180 days old. Someone
  else's quote or a reshare is never person-level. A page's listing date dates its signal
  only if the page yields a single signal. Company pages are extracted once per account.
- Stage 5 (T08): the smart model rates relevance and specificity (one call); code caps a
  company-level signal's specificity at 4 so the person always outranks the company.
  Verifiability: 1 = company's own site, a known outlet, or 2+ outlets; 0.5 = other; 0 =
  unsourced. Sensitive and AI-screening signals are scored but never hooks. The model picks
  from the top 3 (it may prefer #2 or #3 and must say why); a pick outside them is
  overridden by the top score. Best score < 2.5, or the model finding no link to what we
  sell, ends the run low_signal (the draft stage offers a labelled role-based draft).
- Stages 6-7 (T09): drafts are under 120 words, cite every factual sentence, use absolute
  dates, and put no day or date in the ask (the rep may send days later). The critic's
  code checks: length, subject, banned phrases, flattery opener, relative dates, emoji,
  claims known / safe / present, every number traceable to a signal, borrowed words name
  their author. Genericness >= 3/5 fails. One rewrite; a second failure goes to review
  with warnings shown. No hook -> labelled role-based draft (run stays low_signal);
  screening trap -> 3-line outline + banner. Confidence = identity x (hook score / 5) x
  0.8 if warnings remain.
- T11: identity candidates with the same name and the same employer are one person found in
  several places (LinkedIn + RocketReach), not rivals; same name at different employers still
  asks the rep. Schema changes are applied by adding missing columns at startup (no
  destructive migrations at demo scale).
- Identity is certain (1.0, set by code) when the evidence is the prospect's own LinkedIn URL,
  or when the candidate has the same first and last name and the CSV's company domain and the
  evidence checks pass. Exception: two different LinkedIn profiles with that name at that
  company are namesakes, not one person found twice; each is capped at 0.5 and the rep picks,
  unless one of them is the LinkedIn URL in the CSV (that one is kept, the other drops to 0.3).
  Different job titles alone don't make two people (titles vary between pages).
- T12 account plan (case 3): runs at a company with 2+ people still running wait after
  scoring; colleagues who stop earlier leave the group, a colleague resumed later is planned
  alone. Pooled company signals are de-duplicated; person-level signals stay with their
  owner. A low_signal colleague can be rescued by a pooled signal. Persona and send day come
  from seller_profile personas / send_order (title match); no match = no recommended day.
- T12 batch similarity (case 9): 'hook source' = the hook signal's source page; the cap is
  max(1, floor(20% of drafts)); overflow runs (lowest hook score first) re-run from stage 5
  with that source excluded, once. Pairs above 0.8 TF-IDF cosine are flagged on both drafts.
- T13 rules: only the prospect's own reply counts (a colleague's opt-out doesn't suppress
  them); the newest reply wins; opting out beats an open deal, which beats 'not now'. An
  open deal holds the run before research (no credits spent); the guide's optional AE brief
  is not built. Competitor 'usage' = the signal names a seller_profile competitor plus an
  adoption word (adopted, selected, uses, rolled out...); usage within 12 months is held with
  a renewal reminder at purchase + contract length - 90 days; undated or older usage with a
  migration story becomes a displacement hook; a mere mention is never the opener.
- Output is a first-touch email only; no LinkedIn messages, no follow-up sequences.
- Signals older than 180 days are ignored; weights in the hook score are hand-set.
- Public web only. No LinkedIn scraping; rep-pasted notes are trusted as rep input.
- "Recently contacted" is checked against this tool's own history, standing in for a CRM.
- Committee send order (Head of Growth day 0, VP Sales day 2, CMO day 5) is a starting guess.

- T14 crowded hook (case 5): "covered by N outlets" counts the pages we read plus the other
  outlets in the same search's results that report the same event (same category, similar
  headline, dates within 7 days). It costs nothing extra but undercounts: a search returns at
  most 5-10 results, so 5+ outlets means "everyone is covering this".
- A crowded event is context only: it can't open any email (also in the account plan), but it is
  offered to the writer first as sentence-two context. If it is the only signal, the run is
  low_signal. The follow-up search runs once per account, only when an event is crowded
  (about 1 extra credit plus up to 2 page reads); results dated before the event are dropped.
- The critic's crowded check is lexical: a first sentence fails if it is mapped to the event, uses
  the event's words (raise, Series B, round, launch, acquired...) or its amount ($40M).
- The crowded-company test case (Lumen Analytics, Dana Okafor) is fictional, with .example domains.

- T15 review: "edit" means approve with the rep's text (stored with a unified diff); edited text
  is not re-checked, since a person wrote it. Reject keeps an optional reason. Low-signal runs
  (role-based drafts) can be reviewed too; the queue steps through ready_for_review only.
- Revalidation (case 10) runs inside the approve request (a few seconds; the UI shows
  "Re-checking N facts"). An unreadable page counts as gone. A "changed" verdict is only used
  if its new evidence is on the page and the rewritten sentence adds no new numbers; otherwise
  it is treated as gone. Gone re-runs the run from scoring without that source (the same path
  as case 9), so the runner-up becomes the hook and the run returns to the queue.
- Any hiring signal with a role count is time-sensitive in code, whatever the model said.
- Draft age uses the real clock (created_at), not settings.today; /dev/age-draft simulates
  it in demos, and the demo says so.
- The stale-draft test case (Kestrel Labs, Omar Haddad) is fictional, with .example domains.

- T16 dashboard numbers (backend/app/routes/stats.py): "this week" is the last 7 days, not the
  calendar week. "Ready for review" is the share of finished runs whose draft reached the queue
  (ready_for_review, approved or rejected), so reviewing a draft doesn't lower it. Approval rate
  is approved / (approved + rejected); an edit counts as approved. Median run time and credits
  cover finished runs; a resumed run's time includes the wait for the rep. Status counts are
  all-time. Fixture replays report 0 credits and near-zero run times.

- T17 demo data: real companies (ProtoPie, Reco, GetResponse, Abacum) were researched on the live
  web on 19 Oct 2026; the people are from the rep's own prospect export or the company's own press
  release (Reco's executive team). Rows whose case needs invented content (a flan or hostile line
  in a bio, a "not now" reply, a careers page that changes) use fictional companies served by a
  simulated web (seed/simweb/), so nothing invented is ever attached to a real person. The demo
  says so; the case-10 clock is simulated too.
- Live web answers drift (the same query found Ryan Yu on 18 Oct and nothing on 19 Oct), so the
  demo recording reuses earlier successful live recordings of identical requests.
- Reco (not Northwind) is the buying-committee account: the case-3 committee and the case-5 crowded
  raise are shown on one real company. Northwind stays in the unit tests.
- Outlets count as covering the same raise when they state the same amount within the crowded
  window (30 days), even if their headline wording differs.
- Follow-ups tell the hook model what the person replied (trusted CRM data), so it prefers a signal
  that answers it. The critic rejects internal labels such as "person_content" in an email.

- Tech stack change (LangGraph + LangChain + prompts in markdown): done in three steps, each
  checked by the existing tests and the offline demo replay with no re-recording. Prompts moved
  to agents/*.md word for word. LangGraph runs the per-prospect stages only; the batch-level
  similarity check and the review queue stay plain code. LangChain provides the chat client
  (ChatOpenAI) and the Groq-to-Gemini fallback chain; our own strict JSON schema, corrective
  retry, rate-limit waits and recording stay, because they encode free-tier quirks.
- LangGraph costs about 2 ms per prospect (compiling the graph, once, about 10 ms; importing the
  library about 0.3 s at startup). LangSmith (LangChain's cloud tracing) is installed as a
  dependency but off unless someone sets LANGSMITH_TRACING; nothing is sent anywhere.
- Python 3.13 was checked: langgraph 1.2.16, langchain-core 1.6.12, langchain-openai 1.7.2 install
  and run, and both providers answered a live structured call through ChatOpenAI.
- Editing a prompt changes its exact text, so recorded demo answers no longer match: re-record
  (free, uses Groq/Gemini quota) before replaying the demo.
- Retrying a failed run (POST /runs/{id}/retry) restarts at the first stage whose saved result is
  missing, unreadable or stale, and reuses the rest from the saved stage events; no LangGraph
  checkpointer is needed. Web research (gather, extract) older than 24 hours is redone. At most 3
  retries per run (settings.retry_max_attempts); after that the rep uploads the prospect again.
  A retried member of a buying committee is planned alone: its colleagues have already finished.
- Draft versions restart at v1 when a run is redone (retry, batch rebalance), so "the draft to
  review" is the newest record, not the highest version number.
- The critic's factual audit reads the whole email, not just the writer's declared claims. Its
  evidence is exactly what the writer was given (the hook and supporting facts, our offer from
  seller_profile.yaml, the rep's notes); a model's own angle or reasoning never counts. Unsupported
  or contradicted facts that survive the one rewrite block approval; the rep edits, and the edit
  is audited against the same evidence before it is approved. "Uncertain" only warns. Drafts made
  before the audit existed are not blocked. The audit is one fast-model call that also scores
  genericness, so hooked drafts cost no extra call; role-based and outline drafts gain one.
- Written-out quantities ("two", "a dozen", "three hundred") count as numbers in the sourced-numbers
  check; a lone "one" does not ("one question" is not a count).
- When identity fails, code says why (resolve.diagnose) instead of one generic "needs input":
  search_failed (every provider errored; "no results" from all of them still counts as search
  working), bad_data, not_public (the company is real but the person never appears with it),
  ambiguous, unconfirmed. We can never prove a person doesn't exist, so the harshest verdict is
  "looks like bad data", never "doesn't exist".
- bad_data needs hard evidence: a placeholder name (test, asdf, John Doe, digits; kept short so a
  real unusual name like "Na Li" is never called fake), or a company domain that doesn't connect
  at all while no search result mentions it. Any HTTP answer, even a 403 from a bot filter, counts
  as a live site. The site is checked only when nothing else found the company (one request).
- A fixed bad-data row is searched again with the corrected details; it is not trusted the way
  "I know them" (the rep vouching) is.
- Company-level draft: for a not_public person the rep may go ahead on the CSV's details. Identity
  confidence is 0.3 (settings.identity_unverified), research is company-only in practice (person
  results need the full name with the company), the writer is told to say nothing personal, the
  draft carries an "Identity not verified" banner, and approve or edit needs the rep to tick that
  they confirmed the person exists (stored on the draft's review). Reject never needs it.
- A rep can dismiss a needs_input run (status `dismissed`). Dismissed runs count as finished but
  not as "not ready": the rep decided, the agent didn't fall short.
- Hook choice is deliberately flexible: a true, good-enough fact can open an email, because a
  modest real reason to write beats a generic one. Low signal is below 1.5 (was 2.5), recency
  decays over 120 days (was 60: a 4-month-old fact kept only 14% of its points), and facts up to a
  year old are kept (was 180 days). The rating prompt scores an honest indirect link as relevance 2,
  and the hook picker returns "no hook" only when every signal is unrelated to what we sell.
  None of this touches the ethics rules: sensitive news, AI-screening findings and competitor news
  are still never hooks, opted-out and open-deal rules still stop the run, and every claim still
  needs a source (unsupported facts block approval).
- The lists' status column shows how much the research found once it is done: High signal (a
  hook scoring 3.0 or more), Moderate signal (a hook below 3.0), Low signal (facts found, none
  good enough to open with), No signal (no usable fact; AI-screening findings don't count).
  Runs that haven't finished research, or wait on someone (running, needs input, held, blocked,
  failed...), keep their own status: "No signal" there would be misleading. The run page itself
  still shows the real status (ready for review, approved...).
- A `company_focus` signal is always company-level, whoever it names: it is what the company
  says about itself (code rule; the model once filed a CFO's remark about the prospect as theirs).
- Research also searches the company's own site (site:domain, no date window, up to 2 pages).
  What a company states about itself (what it sells, to whom, customers, pricing, integrations,
  stated goals) becomes a `company_focus` signal: often the only real information on a small
  company. Company names are searched without LinkedIn-style extras ("X | tagline", "X (ABC)").
- Research searches wider: 14 fixed searches per prospect (core news, own site + blog, customers
  and case studies, reviews, awards, general news, the person's talks, news and writing) plus up
  to 4 the fast model writes for that prospect (agents/stage3_plan_extra_searches.md). Planned queries must name the
  company or the person, never touch LinkedIn, and use site: only on the company's own domain;
  a planner failure just means no extra searches. To stay on the free tier, the core and own-site
  searches use Tavily first (9 credits, about the old 8) and every wider or planned search tries
  DuckDuckGo first. Up to 14 pages are read (was 10).
- The company relevance gate also accepts a result whose text names the company repeatedly (its
  domain once, or its name twice), not only in the headline: small companies are usually covered
  inside an article. A single passing mention (a listicle) still doesn't count.

## Technical
- Deployed: backend on Render's free plan (render.yaml), frontend on Vercel, Postgres on Neon.
  psycopg 3 is the driver; postgres:// URLs from any host are rewritten to the psycopg dialect.
  Render's disk is ephemeral, so the 24 h search cache restarts with each deploy (harmless).
- Per-visitor hourly caps, in memory (they reset on restart; enough for one free instance): 20
  uploads and 60 prospects sent through the pipeline. Uploads cost nothing; a prospect costs
  about 9 search credits plus model tokens. Files are still capped at 50 rows.
- Prompt files are named by stage and job (agents/stage6_write_and_check_email.md) so a reader
  finds them without opening code; code keeps short keys ("draft") mapped in prompts.AGENT_FILES.
- Zero cost: every service runs on a free tier with no credit card. The LLM is Groq
  (`openai/gpt-oss-120b` smart, `openai/gpt-oss-20b` fast) with Gemini free tier as
  fallback, instead of the Claude API the guide first assumed. Search is Tavily's free
  1,000 credits/month, then DuckDuckGo (`ddgs`, no key), then Exa's free credits.
- Free-tier limits cap volume at roughly 50-60 prospects a day; fixtures mode keeps tests
  and rehearsals at zero usage. Drafts from open-weight models are somewhat weaker than
  frontier paid models; the critic stage and one-hook rule carry most of the quality.
- All LLM providers go through one OpenAI-compatible client, so a paid model can be
  swapped in later through config only.
- Gemini's free tier may use prompts to improve Google's models; acceptable because inputs
  are public web text plus fictional seller data.
- Python 3.13 (the guide said 3.12): it is what this machine's venv uses, and every backend
  dependency ships 3.13 wheels. render.yaml pins 3.13 on the host.
- Next.js 16.3.8 / React 19.2.
- Node 20.11 is below shadcn's stated minimum (20.18.1); the CLI still worked. Upgrade to
  Node 22 LTS before deploying.
- Unsupported facts warn once, then the reviewer decides: a flagged draft (or an edit flagged by
  its one audit) can be approved with "Approve anyway". It is approved with no further audit, and
  draft.review.approved_over_warning records who, when and which sentences were flagged. The
  human in the loop has the final say; the app still never sends anything.
