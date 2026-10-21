"""The pipeline, in order. Spec: docs/GUIDE.md "Pipeline stages in detail"."""

from app.pipeline import account, draft, extract, intake, research, resolve, score
from app.pipeline.runner import Stage

STAGES = [
    Stage("intake", "Intake and guardrails", intake.run, intake.IntakeOutput),
    Stage("resolve", "Resolve identity", resolve.run, resolve.ResolveOutput),
    Stage("gather", "Gather signals", research.run, research.GatherOutput),
    Stage("extract", "Extract signals", extract.run, extract.ExtractOutput),
    Stage("score", "Score and pick hook", score.run, score.ScoreOutput),
    Stage("account_plan", "Account plan", account.run, account.AccountPlanOutput, releases_slot=True),
    Stage("draft", "Draft", draft.run_draft, draft.DraftOutput),
    Stage("critic", "Critic and fix", draft.run_critic, draft.CriticOutput),
]


# What each stage is, for the pipeline diagram (agents/PIPELINE.md): who decides, and which
# agents/*.md file holds its prompts (None = no model involved).
STAGE_INFO = {
    "intake": {"decided_by": "code", "agent": None},
    "resolve": {"decided_by": "fast model + code checks", "agent": "resolve.md"},
    "gather": {"decided_by": "code", "agent": None},
    "extract": {"decided_by": "fast model + code checks", "agent": "extract.md"},
    "score": {"decided_by": "smart model + code", "agent": "score.md"},
    "account_plan": {"decided_by": "smart model + code rules", "agent": "account_plan.md"},
    "draft": {"decided_by": "smart model", "agent": "draft.md"},
    "critic": {"decided_by": "code checks + fast model", "agent": "draft.md"},
}
