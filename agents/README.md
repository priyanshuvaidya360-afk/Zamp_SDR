# Agents: the words every model reads

One file per agent. To change how an agent behaves, edit its file. No Python needed.

| File | Stage | Model | What it decides |
|---|---|---|---|
| [resolve.md](resolve.md) | 2 Resolve identity | fast | which real person the prospect is |
| [extract.md](extract.md) | 4 Extract | fast | what counts as a signal; which sentences are AI traps |
| [score.md](score.md) | 5 Score and pick hook | smart | how signals are rated; how the hook is chosen |
| [account_plan.md](account_plan.md) | Account plan | smart | who opens with what when several people share a company |
| [draft.md](draft.md) | 6-7 Draft and critic | smart / fast | how the email is written; the genericness check |
| [recheck.md](recheck.md) | Review (case 10) | fast / smart | how stale facts are compared and corrected |

The order the agents run in is drawn in [PIPELINE.md](PIPELINE.md).

## How a file is read

- Text before the first `## ` heading is notes for people.
- Each `## name` heading starts one piece of a prompt. The code picks the pieces it needs.
- `{curly_words}` are filled in by the code (names, dates, signals). Keep them as they are.
  A `{word}` the code does not know is an error, so a typo is caught before a model sees it.
- A line that is only `<!-- a comment -->` is ignored.
- Do not start a line inside a section with `## `.

## What is not here

- **Numbers** (score weights, the 2.5 low-signal cutoff, the crowded penalty): `backend/app/config.py`, or a line in `backend/.env`.
- **Who we are** (offer, tone, personas, competitors): `seller_profile.yaml`.
- **Hard rules** (a quote must appear on the page, an opt-out is never contacted): code.

## After you edit a prompt

Edits apply on the next run. The recorded demo (`make dev-api-demo`, `make check-demo`) replays
answers matched by the exact prompt text, so after an edit it needs re-recording:
`cd backend && .venv/bin/python -m scripts.record_demo` (free, but uses Groq/Gemini quota).
