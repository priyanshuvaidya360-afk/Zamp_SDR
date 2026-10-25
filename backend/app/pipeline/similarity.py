"""Batch-level check after every draft is written (case 9: template drift).

Spec: docs/PROJECT_SPEC.md "Account context" case 9 and T12. TF-IDF cosine similarity across the batch's
final drafts flags pairs above 0.8. No single hook source may open more than 20% of the drafts
(at least one is always allowed); the lowest-scoring overflow runs are re-run from stage 5 with
that source excluded. Diversity = 1 - mean pairwise similarity, stored on the batch.
"""

import asyncio
import math
from collections import defaultdict
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlmodel import Session, select

from app import db
from app.models import Batch, DraftRecord, Run, RunStatus, SignalRecord

SIMILAR_ABOVE = 0.8
SOURCE_CAP_SHARE = 0.2


def source_cap(n_drafts: int) -> int:
    return max(1, math.floor(SOURCE_CAP_SHARE * n_drafts))


def similarity_matrix(bodies: list[str]) -> list[list[float]]:
    if len(bodies) < 2:
        return [[1.0] * len(bodies)] * len(bodies)
    tfidf = TfidfVectorizer(stop_words="english").fit_transform(bodies)
    return cosine_similarity(tfidf).tolist()


def diversity(matrix: list[list[float]]) -> float | None:
    n = len(matrix)
    pairs = [matrix[i][j] for i in range(n) for j in range(i + 1, n)]
    return round(1 - sum(pairs) / len(pairs), 3) if pairs else None


def overflow(entries: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Hook source -> runs beyond the cap (the lowest-scoring ones; the strongest keep it)."""
    cap = source_cap(len(entries))
    by_source: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_source[e["source"]].append(e)
    return {
        source: [e["run_id"] for e in sorted(group, key=lambda e: -e["score"])[cap:]]
        for source, group in by_source.items() if len(group) > cap
    }


def _final_drafts(session: Session, batch_id: int) -> list[dict[str, Any]]:
    """One entry per run with a hooked, final draft."""
    entries = []
    runs = session.exec(select(Run).where(Run.batch_id == batch_id, Run.status == RunStatus.ready_for_review)).all()
    for run in runs:
        drafts = session.exec(select(DraftRecord).where(DraftRecord.run_id == run.id).order_by(DraftRecord.id)).all()
        final = next((d for d in reversed(drafts) if d.critic_report.get("final")), None)
        if not final or final.critic_report.get("mode") != "hooked" or final.hook_signal_id is None:
            continue
        hook = session.get(SignalRecord, final.hook_signal_id)
        entries.append({"run_id": run.id, "name": run.full_name or f"{run.first_name} {run.last_name}",
                        "draft": final, "source": hook.source_url if hook else "",
                        "score": (hook.scores or {}).get("score", 0) if hook else 0})
    return entries


def _store(batch_id: int, entries: list[dict[str, Any]], rebalanced: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = similarity_matrix([e["draft"].body for e in entries])
    flagged = [
        {"run_ids": [entries[i]["run_id"], entries[j]["run_id"]],
         "names": [entries[i]["name"], entries[j]["name"]], "similarity": round(matrix[i][j], 3)}
        for i in range(len(entries)) for j in range(i + 1, len(entries)) if matrix[i][j] > SIMILAR_ABOVE
    ]
    result = {"drafts": len(entries), "similar_pairs": flagged, "source_cap": source_cap(len(entries)),
              "rebalanced": rebalanced, "diversity": diversity(matrix)}
    with Session(db.engine) as session:
        batch = session.get(Batch, batch_id)
        batch.diversity_score, batch.similarity = result["diversity"], result
        session.add(batch)
        for pair in flagged:  # each flagged draft says which draft it reads like
            for me, other in ((0, 1), (1, 0)):
                entry = next(e for e in entries if e["run_id"] == pair["run_ids"][me])
                draft = session.get(DraftRecord, entry["draft"].id)
                warning = f"Reads a lot like the draft for {pair['names'][other]} (similarity {pair['similarity']})"
                report = dict(draft.critic_report)
                report["warnings"] = [*report.get("warnings", []), warning]
                draft.critic_report = report
                session.add(draft)
        session.commit()
    return result


async def check_batch(batch_id: int) -> dict[str, Any]:
    from app.pipeline.runner import run_one

    with Session(db.engine) as session:
        entries = _final_drafts(session, batch_id)
    rebalanced = [{"run_id": run_id, "excluded_source": source}
                  for source, run_ids in overflow(entries).items() for run_id in run_ids]
    if rebalanced:  # re-hook the overflow runs, then measure the final drafts
        await asyncio.gather(*(run_one(r["run_id"], start_stage="score",
                                       resume_input={"exclude_sources": [r["excluded_source"]]})
                               for r in rebalanced))
        with Session(db.engine) as session:
            entries = _final_drafts(session, batch_id)
    return _store(batch_id, entries, rebalanced)
