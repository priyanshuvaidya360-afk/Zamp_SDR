"""The pipeline, in order. Spec: docs/PROJECT_SPEC.md "Pipeline stages in detail"."""

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


# What each stage is, for the pipeline diagram (agents/PIPELINE_DIAGRAM.md): who decides, and which
# agents/*.md file holds its prompts (None = no model involved).
STAGE_INFO = {
    "intake": {"decided_by": "code", "agent": None},
    "resolve": {"decided_by": "fast model + code checks", "agent": "stage2_identify_person.md"},
    "gather": {"decided_by": "code + fast model (extra searches)", "agent": "stage3_plan_extra_searches.md"},
    "extract": {"decided_by": "fast model + code checks", "agent": "stage4_extract_signals.md"},
    "score": {"decided_by": "smart model + code", "agent": "stage5_score_and_pick_hook.md"},
    "account_plan": {"decided_by": "smart model + code rules", "agent": "stage5b_buying_committee_plan.md"},
    "draft": {"decided_by": "smart model", "agent": "stage6_write_and_check_email.md"},
    "critic": {"decided_by": "code checks + fast model", "agent": "stage6_write_and_check_email.md"},
}
