from sqlmodel import Session, select

from app.models import Opportunity, SequenceHistory, SequenceOutcome
from scripts.seed_context import seed


def test_seed_twice_does_not_duplicate(session: Session):
    first = seed(session)
    second = seed(session)
    assert first == second == {"crm_accounts": 3, "opportunities": 1, "sequence_history": 2}


def test_seed_contents_match_the_demo_cases(session: Session):
    seed(session)

    priya = session.exec(
        select(SequenceHistory).where(SequenceHistory.email == "priya.raman@acmefintech.com")
    ).one()
    assert priya.outcome == SequenceOutcome.not_now and priya.reply_snippet

    outcomes = {row.outcome for row in session.exec(select(SequenceHistory))}
    assert SequenceOutcome.unsubscribed in outcomes

    deal = session.exec(select(Opportunity)).one()
    assert (deal.account_domain, deal.stage, deal.owner) == ("contosologistics.com", 4, "Sam Patel")

    # Northwind is the buying-committee demo account: it must have no deals.
    assert not session.exec(select(Opportunity).where(Opportunity.account_domain.contains("northwind"))).all()
