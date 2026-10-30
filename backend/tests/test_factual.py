"""The critic's factual audit: the whole email is checked against the facts the writer was given,
whatever the writer declared in `claims`; unsupported facts block approval until a rep fixes them."""

import pytest
from sqlmodel import select

from app.models import DraftRecord, ReviewerAction, Run, RunStatus
from app.pipeline import draft
from app.pipeline.draft import Evidence, factual_report, spell_out_numbers
from app.review import ReviewError, latest_draft, review
from tests.test_draft import GOOD, GOOD_BODY, GOOD_CLAIM, check, ctx, fake_models  # noqa: F401 (fixture)

INVENTED = "Abacum also raised $40M in September."


def claim(sentence: str, status: str, fact_id: str | None = None, suggestion: str | None = None) -> dict:
    return {"sentence": sentence, "about": "company", "status": status, "fact_id": fact_id,
            "reason": f"marked {status}", "suggestion": suggestion}


SUPPORTED = claim(GOOD_CLAIM["sentence"], "supported", "1")
FUNDED = {**GOOD, "body": GOOD_BODY.replace("Open to", f"{INVENTED} Open to")}  # declared claims: launch only


# --- Code: numbers, links, report rules ----------------------------------------------------------


def test_written_out_quantities_become_numbers():
    assert spell_out_numbers("hiring two SDRs and a dozen AEs") == "hiring 2 SDRs and a 12 AEs"
    assert spell_out_numbers("three hundred customers, twenty-five reps") == "300 customers, 25 reps"
    assert spell_out_numbers("One question: one of your teams") == "One question: one of your teams"


def test_invented_hiring_numbers_are_caught_in_words_and_digits():
    assert "numbers_sourced" in check(body=GOOD_BODY.replace("Open to", "You are hiring two SDRs. Open to"))
    assert "numbers_sourced" in check(body=GOOD_BODY.replace("Open to", "You are hiring 7 SDRs. Open to"))
    assert "numbers_sourced" not in check(body=GOOD_BODY.replace("Open to", "Acme is hiring four SDRs. Open to"),
                                          claims=[GOOD_CLAIM, {"sentence": "Acme is hiring four SDRs.", "signal_id": 2}])


def test_links_are_not_allowed():
    assert "no_links" in check(body=GOOD_BODY.replace("Open to", "See https://halyard.example/demo. Open to"))


EVIDENCE = [Evidence(id="1", text='product_launch, 13 October 2026: Abacum launched Modeler. Evidence: "Abacum '
                                  'launches Modeler"', source_url="https://abacum.ai/news"),
            Evidence(id="seller", text="What we sell: outbound pipeline")]


def report(claims: list[dict], body: str = FUNDED["body"], **kw) -> dict:
    return factual_report(draft._Audit(claims=claims, genericness=0, genericness_reason=""), EVIDENCE,
                          "modeler and pipeline", body, **kw)


def test_report_trusts_code_not_the_model():
    r = report([SUPPORTED,
                claim(INVENTED, "supported", "7"),  # cites a fact that was never given
                claim("Abacum is the market leader.", "unsupported")])  # not in the email at all
    assert r["supported"] == 1 and r["unsupported"] == 1 and r["blocking"]
    assert [i["sentence"] for i in r["issues"]] == [INVENTED]
    assert r["issues"][0]["reason"] == "It cites no fact that was given to the writer."


def test_seller_claims_are_checked_against_the_seller_profile_and_ids_are_normalised():
    offer = "Our outbound pipeline helps you."
    body = GOOD_BODY.replace("Open to", f"{offer} We also doubled reply rates for Acme. Open to")
    r = report([{**SUPPORTED, "fact_id": "[1]"},
                {**claim(offer, "unsupported"), "about": "seller"},  # the model wrongly flags our own offer
                {**claim("We also doubled reply rates for Acme.", "supported"), "about": "seller"}], body=body)
    assert r["supported"] == 2 and r["unsupported"] == 1
    assert [i["sentence"] for i in r["issues"]] == ["We also doubled reply rates for Acme."]


def test_a_model_flag_on_a_sentence_a_fact_states_only_warns():
    restated = "Abacum launched Modeler on 13 October."
    r = report([claim(restated, "unsupported")], body=GOOD_BODY)
    assert not r["blocking"] and r["issues"][0]["status"] == "uncertain"
    changed = GOOD_BODY.replace("13 October", "19 October")  # same words, a number the fact doesn't have
    r = report([claim("Abacum launched Modeler on 19 October.", "unsupported")], body=changed)
    assert r["blocking"]


def test_supported_claims_pass_and_uncertain_only_warns():
    r = report([SUPPORTED, claim("New products need pipeline from scratch, and hiring SDRs for it is slow.",
                                 "uncertain")], body=GOOD_BODY)
    assert not r["blocking"] and r["supported"] == 1 and r["uncertain"] == 1
    assert r["issues"][0]["status"] == "uncertain"


def test_contradicted_claim_blocks_and_shows_its_evidence():
    r = report([claim(GOOD_CLAIM["sentence"], "contradicted", "1")], body=GOOD_BODY)
    assert r["blocking"] and r["contradicted"] == 1
    assert r["issues"][0]["evidence"].startswith("product_launch") and r["issues"][0]["source_url"]


def test_edit_check_finds_numbers_the_model_missed():
    r = report([SUPPORTED], body=GOOD_BODY.replace("Open to", "Your team grew by thirty reps. Open to"),
               check_numbers=True)
    assert r["blocking"] and "30" in r["issues"][0]["reason"]


# --- Critic flow (mocked models) ------------------------------------------------------------------


async def test_unsupported_fact_missing_from_claims_triggers_one_targeted_rewrite(ctx, monkeypatch):
    prompts = fake_models(monkeypatch, [FUNDED, GOOD], generic=[1, 1],
                          audits=[[SUPPORTED, claim(INVENTED, "unsupported")], [SUPPORTED]])
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    out = await draft.run_critic(ctx)

    assert out.rewritten and out.passed and out.version == 2
    assert f'unsupported_fact: "{INVENTED}"' in prompts[1]
    assert "Change only the sentences these problems name" in prompts[1]
    assert GOOD_CLAIM["sentence"] not in [p for p in out.warnings]
    assert "unsupported ones removed by the rewrite" in out.message


async def test_still_unsupported_after_the_rewrite_is_blocked_not_rewritten_again(ctx, monkeypatch, session):
    prompts = fake_models(monkeypatch, [FUNDED, FUNDED], generic=[1, 1],
                          audits=[[claim(INVENTED, "unsupported")], [claim(INVENTED, "unsupported")]])
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    out = await draft.run_critic(ctx)

    assert len(prompts) == 2 and out.version == 2 and not out.passed
    assert out.message.startswith("Manual fix needed")
    assert ctx.final_reason == "1 unsupported factual claim: edit it before approving"
    final = session.exec(select(DraftRecord).where(DraftRecord.run_id == ctx.run_id, DraftRecord.version == 2)).one()
    assert final.critic_report["factual"]["blocking"]
    assert final.critic_report["factual"]["evidence"][0]["id"] == "1"


async def test_role_based_draft_with_an_invented_trigger_is_caught(ctx, monkeypatch):
    ctx.outputs["score"].hook = None
    ctx.final_status = RunStatus.low_signal
    made_up = "Congrats on opening your London office."
    role = {"subject": "outbound", "body": f"Hi Amy,\n{made_up} Teams like yours find SDR hiring slow.\nAlex",
            "claims": []}
    fake_models(monkeypatch, [role, role], generic=[],
                audits=[[claim(made_up, "unsupported")], [claim(made_up, "unsupported")]])
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    out = await draft.run_critic(ctx)
    assert out.rewritten and "unsupported" in out.message and ctx.final_status == RunStatus.low_signal


# --- Review gate ----------------------------------------------------------------------------------


async def blocked_draft(ctx, monkeypatch, session) -> int:
    fake_models(monkeypatch, [FUNDED, FUNDED], generic=[1, 1],
                audits=[[claim(INVENTED, "unsupported")], [claim(INVENTED, "unsupported")]])
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    await draft.run_critic(ctx)
    run = session.get(Run, ctx.run_id)
    run.status = RunStatus.ready_for_review
    session.add(run)
    session.commit()
    return ctx.run_id


async def test_approve_is_refused_while_facts_are_unsupported(ctx, monkeypatch, session):
    run_id = await blocked_draft(ctx, monkeypatch, session)
    with pytest.raises(ReviewError, match="1 unsupported factual claim"):
        await review(session, run_id, ReviewerAction.approve)
    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.ready_for_review


async def test_an_edit_is_audited_before_it_is_approved(ctx, monkeypatch, session):
    run_id = await blocked_draft(ctx, monkeypatch, session)

    fake_models(monkeypatch, [], generic=[], audits=[[claim(INVENTED, "unsupported")]])
    still_wrong = await review(session, run_id, ReviewerAction.edit, body=FUNDED["body"] + " ")
    assert still_wrong.outcome == "needs_fix" and still_wrong.factual["blocking"]
    assert session.get(Run, run_id).status == RunStatus.ready_for_review

    fake_models(monkeypatch, [], generic=[], audits=[[SUPPORTED]])
    fixed = await review(session, run_id, ReviewerAction.edit, body=GOOD_BODY)
    assert fixed.outcome == "edited" and fixed.run_status == RunStatus.approved


async def test_after_one_warning_the_rep_can_approve_an_edit_anyway(ctx, monkeypatch, session):
    run_id = await blocked_draft(ctx, monkeypatch, session)
    fake_models(monkeypatch, [], generic=[], audits=[[claim(INVENTED, "unsupported")]])
    edited = FUNDED["body"] + " "
    warned = await review(session, run_id, ReviewerAction.edit, body=edited)
    assert warned.outcome == "needs_fix"

    fake_models(monkeypatch, [], generic=[], audits=[])  # no second audit: an LLM call here would fail
    approved = await review(session, run_id, ReviewerAction.edit, body=edited, approve_anyway=True)
    assert approved.outcome == "edited" and approved.run_status == RunStatus.approved
    stored = latest_draft(session, run_id)
    assert stored.edited_body == edited.strip()
    override = stored.review["approved_over_warning"]
    assert override["by"] == "rep" and override["sentences"][0]["sentence"] == INVENTED


async def test_the_rep_can_approve_a_flagged_draft_anyway(ctx, monkeypatch, session):
    run_id = await blocked_draft(ctx, monkeypatch, session)
    result = await review(session, run_id, ReviewerAction.approve, approve_anyway=True)
    assert result.outcome == "approved" and result.run_status == RunStatus.approved
    assert latest_draft(session, run_id).review["approved_over_warning"]["sentences"][0]["sentence"] == INVENTED
