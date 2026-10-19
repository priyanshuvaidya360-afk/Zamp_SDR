# Assumptions

Decisions and assumptions made while building Signal. Add to this as you build
(see docs/GUIDE.md, "Assumptions and interview talking points").

## Product
- The seller is fictional but specific: Halyard, an outbound pipeline engine sold to growth,
  marketing and sales leaders at B2B SaaS companies (`seller_profile.yaml`). It replaced an
  AP-automation seller (Ledgerline) on 2026-10-19 because the real prospect list we demo
  with is Heads of Growth and CMOs; against an AP seller most rows correctly scored
  low_signal. Relevance scoring, hiring searches and personas all read this file.
- Input is a CSV of up to 50 prospects per upload; larger files and CRM import are next steps.
- Each prospect needs first name, last name, and a company domain or LinkedIn URL. Company
  name alone is not a unique identity.
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

## Technical
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
- Python 3.15 instead of the guide's 3.12: it is what this machine has, and every backend
  dependency ships 3.15 wheels. Pin the version on the backend host at deploy time.
- Next.js 16.3.8 / React 19.2.
- Node 20.11 is below shadcn's stated minimum (20.18.1); the CLI still worked. Upgrade to
  Node 22 LTS before deploying.
