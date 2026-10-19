# Assumptions

Decisions and assumptions made while building Signal. Add to this as you build
(see docs/GUIDE.md, "Assumptions and interview talking points").

## Product
- The seller is fictional but specific: Ledgerline, an AP automation platform selling to
  finance leaders (`seller_profile.yaml`). Relevance scoring depends on it.
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
- Output is a first-touch email only; no LinkedIn messages, no follow-up sequences.
- Signals older than 180 days are ignored; weights in the hook score are hand-set.
- Public web only. No LinkedIn scraping; rep-pasted notes are trusted as rep input.
- "Recently contacted" is checked against this tool's own history, standing in for a CRM.
- Committee send order (AP Manager day 0, Controller day 2, CFO day 5) is a starting guess.

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
