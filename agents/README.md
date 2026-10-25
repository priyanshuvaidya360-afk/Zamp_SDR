# Agents: the words every model reads

One file per agent. To change how an agent behaves, edit its file. No Python needed.

| File | Stage | Model | What it decides |
|---|---|---|---|
| [stage2_identify_person.md](stage2_identify_person.md) | 2 Resolve identity | fast | which real person the prospect is |
| [stage3_plan_extra_searches.md](stage3_plan_extra_searches.md) | 3 Gather | fast | which extra searches to run for this prospect (on top of the fixed ones) |
| [stage4_extract_signals.md](stage4_extract_signals.md) | 4 Extract | fast | what counts as a signal; which sentences are AI traps |
| [stage5_score_and_pick_hook.md](stage5_score_and_pick_hook.md) | 5 Score and pick hook | smart | how signals are rated; how the hook is chosen |
| [stage5b_buying_committee_plan.md](stage5b_buying_committee_plan.md) | Account plan | smart | who opens with what when several people share a company |
| [stage6_write_and_check_email.md](stage6_write_and_check_email.md) | 6-7 Draft and critic | smart / fast | how the email is written; the factual audit and genericness check |
| [stage8_recheck_stale_facts.md](stage8_recheck_stale_facts.md) | Review (case 10) | fast / smart | how stale facts are compared and corrected |

The order the agents run in is drawn in [PIPELINE_DIAGRAM.md](PIPELINE_DIAGRAM.md).

## Every prompt has the same six parts

Each model call has a system part (`## system`, `## rate_system`, ...) and an input part (`## user`, `## rate_user`, ...):

| Part | Where | What goes in it |
|---|---|---|
| 1. Role | system | who the model is, in one line |
| 2. Goal and task | system | what it must achieve, and the job to do |
| 3. Rules | system | every rule, in short plain sentences; allowed values listed exactly |
| 4. Output contract | system | the exact fields to return (they match the code's schema) |
| 5. Examples | system | one or two short, made-up examples |
| 6. Input | user | the data, filled in by the code through `{curly_words}` |

Rules that need a value from the code (a word limit, the rep's name) live in the input part, because the system part is loaded without values. Small templates the code uses to build an input (one line per signal, an optional extra rule) sit below it, under an "Input pieces" comment.

## How a file is read

- Text before the first `## ` heading is notes for people.
- Each `## name` heading starts one piece of a prompt. The code picks the pieces it needs.
- `{curly_words}` are filled in by the code (names, dates, signals). Keep them as they are.
  A `{word}` the code does not know is an error, so a typo is caught before a model sees it.
- A line that is only `<!-- a comment -->` is ignored.
- Do not start a line inside a section with `## `.

## What is not here

- **Numbers** (score weights, the 1.5 low-signal cutoff, the 3.0 high-signal mark, the crowded penalty): `backend/app/config.py`, or a line in `backend/.env`.
- **Who we are** (offer, tone, personas, competitors): `seller_profile.yaml`.
- **Hard rules** (a quote must appear on the page, an opt-out is never contacted): code.

## After you edit a prompt

Edits apply on the next run. The recorded demo (`make dev-api-demo`, `make check-demo`) replays
answers matched by the exact prompt text, so after an edit it needs re-recording:
`cd backend && .venv/bin/python -m scripts.record_demo` (free, but uses Groq/Gemini quota).
