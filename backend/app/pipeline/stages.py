"""The pipeline, in order. Real stages replace the stand-ins task by task (T06-T09)."""

from app.pipeline import intake, research, resolve, stubs
from app.pipeline.runner import Stage

STAGES = [
    Stage("intake", "Intake and guardrails", intake.run, intake.IntakeOutput),
    Stage("resolve", "Resolve identity", resolve.run, resolve.ResolveOutput),
    Stage("gather", "Gather signals", research.run, research.GatherOutput),
    *[s for s in stubs.STAGES if s.name not in {"intake", "resolve", "gather"}],
]
