"""T12: account plan (case 3) and batch similarity (case 9), with the model mocked."""

import asyncio
import re
from datetime import date

import pytest
from sqlmodel import Session, select

from app import ingest, llm
from app.config import settings
from app.llm import Usage
from app.models import Batch, DraftRecord, Run, RunStatus, SignalRecord
from app.pipeline import account, extract, runner, similarity
from app.pipeline.account import _Plan
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.runner import Stage, StopRun
from app.pipeline.score import ScoredSignal, ScoreOutput, effective_score
from app.pipeline.stubs import StubOutput
from app.routes.batches import _store

NORTHWIND = (
    "first_name,last_name,company,title,company_domain\n"
    "Carla,Moss,Northwind,CMO,northwind.com\n"
    "Gita,Rao,Northwind,Head of Growth,northwind.com\n"
    "Victor,Lane,Northwind,VP Sales,northwind.com\n"
)
COMPANY = [  # (summary, type, score) shared by everyone at Northwind
    ("Northwind raised a $60M Series C", "funding", 4.5),
    ("Northwind is hiring 6 SDRs", "hiring", 3.5),
    ("Northwind appointed a new VP Marketing", "leadership_change", 3.4),
]
GITA_ONLY = ("Gita spoke on a podcast about outbound experiments", "person_content", 3.8)


def make_batch(session: Session, text: str = NORTHWIND) -> list[int]:
    batch = Batch(filename="northwind.csv")
    _store(batch, ingest.parse_upload(text.encode()))
    session.add(batch)
    session.commit()
    return runner.create_runs(session, batch)


def stages(seen: dict, stop_for: str | None = None, no_hook_for: str | None = None):
    """extract + score stand-ins that build each person's signals, then the real account plan,
    then a draft stand-in that records the hook the draft would use."""

    async def fake_extract(ctx):
        if ctx.row["first_name"] == stop_for:
            raise StopRun(RunStatus.needs_input, "2 people could match")
        items = list(COMPANY) + ([GITA_ONLY] if ctx.row["first_name"] == "Gita" else [])
        signals = [ExtractedSignal(type=t, about="person" if t == "person_content" else "company", summary=s,
                                   evidence=s, source_url=f"https://news.example.com/{i}", sensitive=False,
                                   published_at=date(2026, 10, 1)) for i, (s, t, _) in enumerate(items)]
        extract._store(ctx, signals)
        return ExtractOutput(message="", signals=signals)

    async def fake_score(ctx):
        signals = ctx.outputs["extract"].signals
        scores = {s: sc for s, _, sc in [*COMPANY, GITA_ONLY]}
        ranked = sorted((ScoredSignal(signal_id=s.id, type=s.type, about=s.about, summary=s.summary,
                                      source_url=s.source_url, score=scores[s.summary]) for s in signals),
                        key=lambda r: -r.score)
        if ctx.row["first_name"] == no_hook_for:
            from app.models import RunStatus as RS
            ctx.final_status, ctx.final_reason = RS.low_signal, "Signal too weak"
            return ScoreOutput(message="", ranked=ranked, low_signal=True)
        with Session(runner.db.engine) as session:  # stage 5 alone would open with the Series C
            record = session.get(SignalRecord, ranked[0].signal_id)
            record.selected = True
            session.add(record)
            session.commit()
        return ScoreOutput(message="", hook=ranked[0], angle="solo angle", ranked=ranked)

    async def fake_draft(ctx):
        hook = effective_score(ctx).hook
        seen[ctx.row["first_name"]] = hook.summary if hook else None
        return StubOutput(message="drafted")

    return [Stage("extract", "Extract", fake_extract, ExtractOutput),
            Stage("score", "Score", fake_score, ScoreOutput),
            Stage("account_plan", "Account plan", account.run, account.AccountPlanOutput, releases_slot=True),
            Stage("draft", "Draft", fake_draft, StubOutput)]


def fake_planner(monkeypatch, *plans):
    """Each call returns the next plan, written in terms of signal summaries -> prompt ids."""
    prompts = []

    async def structured(role, system, user, schema, providers=None):
        prompts.append(user)
        pid = {m.group(2).split(" | ")[3]: int(m.group(1))
               for m in re.finditer(r"^S(\d+) \| (.+)$", user, re.M)}
        members = {name: int(n) for n, name in re.findall(r"^M(\d+): (\w+)", user, re.M)}
        picks = [{"member": members[who], "opener": pid.get(opener) if opener else None,
                  "context": pid.get(context) if context else None, "angle": f"angle for {who}"}
                 for who, opener, context in plans[min(len(prompts) - 1, len(plans) - 1)] if who in members]
        return _Plan(picks=picks, reasoning="test"), Usage(provider="groq", model="m", role=role)

    monkeypatch.setattr(llm, "structured", structured)
    return prompts


ALL_SERIES_C = [("Carla", COMPANY[0][0], None), ("Gita", COMPANY[0][0], None), ("Victor", COMPANY[0][0], None)]
GOOD = [("Carla", COMPANY[2][0], COMPANY[0][0]), ("Gita", GITA_ONLY[0], None), ("Victor", COMPANY[1][0], None)]


# --- Case 3: Done when -----------------------------------------------------------------------


async def test_three_northwind_people_get_three_different_openers(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen))
    prompts = fake_planner(monkeypatch, ALL_SERIES_C, GOOD)
    run_ids = make_batch(session)
    await asyncio.wait_for(runner.run_batch(run_ids), 10)

    assert seen == {"Carla": COMPANY[2][0], "Gita": GITA_ONLY[0], "Victor": COMPANY[1][0]}
    assert len(set(seen.values())) == 3
    assert len(prompts) == 2 and "open more than one email: S1" in prompts[1]  # rejected, violation named

    session.expire_all()
    runs = {r.first_name: r for r in session.exec(select(Run))}
    assert {n: r.send_day for n, r in runs.items()} == {"Carla": 5, "Gita": 0, "Victor": 2}
    plan = runs["Gita"].account_plan
    assert plan["counterfactual"] == f"Without the account plan, all 3 would have opened with the same signal: {COMPANY[0][0]}"
    assert {c["name"]: c["passed"] for c in plan["checks"]} == {"no_reused_openers": True, "company_context_once": True}
    assert not plan["fallback"] and plan["attempts"] == 2
    victor_series_c = session.exec(select(SignalRecord).where(SignalRecord.run_id == runs["Victor"].id,
                                                              SignalRecord.summary == COMPANY[0][0])).one()
    assert not victor_series_c.selected and victor_series_c.rejected_reason.startswith("Account plan")


async def test_a_plan_that_breaks_rules_twice_falls_back_to_code(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen))
    fake_planner(monkeypatch, ALL_SERIES_C, ALL_SERIES_C)
    await asyncio.wait_for(runner.run_batch(make_batch(session)), 10)
    assert len(set(seen.values())) == 3  # code still keeps openers distinct
    session.expire_all()
    plan = session.exec(select(Run)).first().account_plan
    assert plan["fallback"] and any(c["name"] == "model_plan" and not c["passed"] for c in plan["checks"])


async def test_a_colleague_who_stops_early_does_not_block_the_others(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen, stop_for="Victor"))
    fake_planner(monkeypatch, GOOD)
    await asyncio.wait_for(runner.run_batch(make_batch(session)), 10)
    assert set(seen) == {"Carla", "Gita"} and seen["Carla"] != seen["Gita"]


async def test_waiting_colleagues_release_their_slot(session, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 1)  # 3 people, 1 slot: must not deadlock
    runner._semaphores.clear()
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen))
    fake_planner(monkeypatch, GOOD)
    await asyncio.wait_for(runner.run_batch(make_batch(session)), 10)
    assert len(seen) == 3
    runner._semaphores.clear()


async def test_low_signal_colleague_is_rescued_by_a_pooled_signal(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen, no_hook_for="Victor"))
    fake_planner(monkeypatch, GOOD)
    await asyncio.wait_for(runner.run_batch(make_batch(session)), 10)
    session.expire_all()
    victor = session.exec(select(Run).where(Run.first_name == "Victor")).one()
    assert seen["Victor"] == COMPANY[1][0] and victor.status == RunStatus.ready_for_review


async def test_only_person_at_a_company_skips_the_plan(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "get_stages", lambda: stages(seen))
    prompts = fake_planner(monkeypatch, GOOD)
    one = "first_name,last_name,company,title,company_domain\nCarla,Moss,Northwind,CMO,northwind.com\n"
    await asyncio.wait_for(runner.run_batch(make_batch(session, one)), 10)
    assert prompts == [] and seen == {"Carla": COMPANY[0][0]}


def test_validation_names_every_broken_rule():
    members = [account.Member(run_id=i, name=n) for i, n in ((1, "Carla"), (2, "Gita"))]
    pooled = [account.PooledSignal(pid=1, summary="a", type="funding", about="company", source_url="u"),
              account.PooledSignal(pid=2, summary="b", type="hiring", about="company", source_url="v"),
              account.PooledSignal(pid=3, summary="c", type="person_content", about="person", source_url="w", owner=2)]
    bad = _Plan(picks=[{"member": 1, "opener": 3, "context": 1, "angle": ""},
                       {"member": 2, "opener": 2, "context": 1, "angle": ""}], reasoning="")
    problems = account.violations(bad, members, pooled)
    assert any("S3 is about another person" in p for p in problems)
    assert any("context for at most one person" in p for p in problems)


# --- Case 9: Done when -----------------------------------------------------------------------


def test_source_cap_and_overflow():
    assert [similarity.source_cap(n) for n in (1, 3, 5, 10, 25)] == [1, 1, 1, 2, 5]
    entries = [{"run_id": i, "source": "x" if i < 4 else f"s{i}", "score": 5 - i * 0.1} for i in range(10)]
    assert similarity.overflow(entries) == {"x": [2, 3]}  # the two lowest-scoring 'x' runs


async def test_ten_drafts_trip_the_cap_and_recover(session, monkeypatch):
    """4 of 10 drafts open on the same press release; the cap is 2, so 2 are re-hooked."""
    batch = Batch(filename="ten.csv")
    session.add(batch)
    session.commit()
    rehooked = []
    for i in range(10):
        run = Run(batch_id=batch.id, row_number=i + 2, first_name=f"P{i}", last_name="Q",
                  status=RunStatus.ready_for_review)
        session.add(run)
        session.commit()
        source = "https://pr.example.com/same-release" if i < 4 else f"https://news{i}.example.com/a"
        hook = SignalRecord(run_id=run.id, type="funding", about="company", summary=f"s{i}", evidence="e",
                            source_url=source, scores={"score": 4 - i * 0.1}, selected=True)
        session.add(hook)
        session.commit()
        body = ("Saw the same funding release everyone read. " * 3) if i < 4 else f"Unique note {i} about topic {i * 7}."
        session.add(DraftRecord(run_id=run.id, subject="s", body=body, hook_signal_id=hook.id,
                                critic_report={"final": True, "mode": "hooked", "warnings": []}))
        session.commit()

    async def fake_rerun(run_id, start_stage=None, resume_input=None):
        """Stands in for re-running stages 5-7 with the source excluded: a new hook and draft."""
        assert start_stage == "score" and resume_input == {"exclude_sources": ["https://pr.example.com/same-release"]}
        rehooked.append(run_id)
        with Session(runner.db.engine) as s:
            new = SignalRecord(run_id=run_id, type="hiring", about="company", summary="hiring", evidence="e",
                               source_url=f"https://careers.example.com/{run_id}", scores={"score": 3.0}, selected=True)
            s.add(new)
            s.commit()
            s.add(DraftRecord(run_id=run_id, version=2, subject="s", body=f"Different opener about hiring {run_id}.",
                              hook_signal_id=new.id, critic_report={"final": True, "mode": "hooked", "warnings": []}))
            s.commit()

    monkeypatch.setattr(runner, "run_one", fake_rerun)
    result = await similarity.check_batch(batch.id)

    assert len(rehooked) == 2 and result["source_cap"] == 2
    assert {r["excluded_source"] for r in result["rebalanced"]} == {"https://pr.example.com/same-release"}
    sources = [e["source"] for e in similarity._final_drafts(session, batch.id)]
    assert sources.count("https://pr.example.com/same-release") == 2  # recovered: within the cap
    session.expire_all()
    stored = session.get(Batch, batch.id)
    assert stored.diversity_score == result["diversity"] and 0 < stored.diversity_score < 1
    assert result["similar_pairs"] and result["similar_pairs"][0]["similarity"] > 0.8  # the 2 identical left
    for run_id in result["similar_pairs"][0]["run_ids"]:  # both drafts of the pair say so
        final = session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id)
                             .order_by(DraftRecord.id.desc())).first()
        assert any(w.startswith("Reads a lot like the draft for") for w in final.critic_report["warnings"])


def test_batch_balance_exclusion_rule(session):
    from app.pipeline import score

    ctx = type("Ctx", (), {"resume_input": {"exclude_sources": ["https://x.example"]}})()
    hit = ExtractedSignal(type="funding", about="company", summary="s", evidence="e",
                          source_url="https://x.example", sensitive=False)
    assert score._batch_balance(hit, ctx).startswith("Batch balance")
    assert score._batch_balance(hit.model_copy(update={"source_url": "https://y.example"}), ctx) is None
