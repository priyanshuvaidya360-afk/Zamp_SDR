"""Stand-in stages for runner and API tests: each waits settings.stub_stage_delay_s and
returns a dummy result, so the flow can be tested without models or search.
"""

import asyncio
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.pipeline.runner import RunContext, Stage


class StubOutput(BaseModel):
    message: str
    data: dict[str, Any] = {}


def _stub(message: str):
    async def run(ctx: RunContext) -> StubOutput:
        await asyncio.sleep(settings.stub_stage_delay_s)
        return StubOutput(message=message)

    return run


async def _gather(ctx: RunContext) -> StubOutput:
    async def company_research() -> dict[str, Any]:
        await asyncio.sleep(settings.stub_stage_delay_s)
        return {"company": ctx.row["company"], "signals": []}

    shared = await ctx.batch.company_research(ctx.account_key, company_research)
    return StubOutput(message="Stub: gathered 0 sources", data={"company_research": shared})


STAGES = [
    Stage("intake", "Intake and guardrails", _stub("Stub: no recent contact, no open deal"), StubOutput),
    Stage("resolve", "Resolve identity", _stub("Stub: found 1 match, confidence 0.92"), StubOutput),
    Stage("gather", "Gather signals", _gather, StubOutput),
    Stage("extract", "Extract signals", _stub("Stub: extracted 0 signals"), StubOutput),
    Stage("score", "Score and pick hook", _stub("Stub: picked a hook"), StubOutput),
    Stage("draft", "Draft", _stub("Stub: wrote a 90-word draft"), StubOutput),
    Stage("critic", "Critic and fix", _stub("Stub: draft passed all checks"), StubOutput),
]
