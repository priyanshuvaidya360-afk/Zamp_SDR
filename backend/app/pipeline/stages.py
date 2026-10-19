"""The pipeline, in order. Real stages replace the stand-ins task by task (T06-T09)."""

from app.pipeline import extract, intake, research, resolve, score, stubs
from app.pipeline.runner import Stage

REAL = {"intake", "resolve", "gather", "extract", "score"}

STAGES = [
    Stage("intake", "Intake and guardrails", intake.run, intake.IntakeOutput),
    Stage("resolve", "Resolve identity", resolve.run, resolve.ResolveOutput),
    Stage("gather", "Gather signals", research.run, research.GatherOutput),
    Stage("extract", "Extract signals", extract.run, extract.ExtractOutput),
    Stage("score", "Score and pick hook", score.run, score.ScoreOutput),
    *[s for s in stubs.STAGES if s.name not in REAL],
]
