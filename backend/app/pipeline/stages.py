"""The pipeline, in order. Spec: docs/GUIDE.md "Pipeline stages in detail"."""

from app.pipeline import draft, extract, intake, research, resolve, score
from app.pipeline.runner import Stage

STAGES = [
    Stage("intake", "Intake and guardrails", intake.run, intake.IntakeOutput),
    Stage("resolve", "Resolve identity", resolve.run, resolve.ResolveOutput),
    Stage("gather", "Gather signals", research.run, research.GatherOutput),
    Stage("extract", "Extract signals", extract.run, extract.ExtractOutput),
    Stage("score", "Score and pick hook", score.run, score.ScoreOutput),
    Stage("draft", "Draft", draft.run_draft, draft.DraftOutput),
    Stage("critic", "Critic and fix", draft.run_critic, draft.CriticOutput),
]
