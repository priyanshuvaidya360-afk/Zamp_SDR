# Assumptions

Decisions and assumptions made while building Signal. Add to this as you build
(see docs/GUIDE.md, "Assumptions and interview talking points").

## Product
- The seller is fictional but specific: Ledgerline, an AP automation platform selling to
  finance leaders (`seller_profile.yaml`). Relevance scoring depends on it.
- Input is a CSV of up to 50 prospects per upload; larger files and CRM import are next steps.
- Each prospect needs first name, last name, and a company domain or LinkedIn URL. Company
  name alone is not a unique identity.
- Output is a first-touch email only; no LinkedIn messages, no follow-up sequences.
- Signals older than 180 days are ignored; weights in the hook score are hand-set.
- Public web only. No LinkedIn scraping; rep-pasted notes are trusted as rep input.
- "Recently contacted" is checked against this tool's own history, standing in for a CRM.
- Committee send order (AP Manager day 0, Controller day 2, CFO day 5) is a starting guess.

## Technical
- Python 3.15 instead of the guide's 3.12: it is what this machine has, and every backend
  dependency ships 3.15 wheels. Pin the version on the backend host at deploy time.
- Next.js 16.3.8 / React 19.2.
- Node 20.11 is below shadcn's stated minimum (20.18.1); the CLI still worked. Upgrade to
  Node 22 LTS before deploying.
