"""The pipeline as a LangGraph graph: one node per stage, in order.

Spec: docs/GUIDE.md "Pipeline stages in detail". `python -m scripts.draw_pipeline` draws this
graph into agents/PIPELINE.md.

What the graph does (and nothing more):
- runs the stages in order, one node each
- after a node, goes on to the next stage, unless the stage stopped or failed the run
  (the dotted arrows to the end): then every later stage is marked skipped
- a resumed run enters at its start stage; earlier nodes pass straight through

What each node does is what one step of the old loop did: record "started", run the stage, then
record "done" (with its output), "done" with a stop reason, or "failed". Every event is saved
and streamed as before, so the run view and the batch view see no difference.
"""

import time
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.models import RunStatus, StageState
from app.pipeline import runner
from app.pipeline.runner import RunContext, Stage, StopRun


class RunState(TypedDict, total=False):
    status: RunStatus | None  # set when the run ends early, or by the finish node
    reason: str | None


@dataclass
class Runtime:
    """What the nodes need for one run (passed through the graph's config, not its state)."""

    ctx: RunContext
    slot: Any  # the concurrency semaphore (runner._semaphore())
    start: int = 0  # index of the first stage to run (a resumed run starts later)


def _runtime(config: RunnableConfig) -> Runtime:
    return config["configurable"]["runtime"]


def _stage_node(stages: list[Stage], index: int):
    stage = stages[index]

    async def node(state: RunState, config: RunnableConfig) -> RunState:
        rt = _runtime(config)
        if index < rt.start:
            return {}  # resumed run: this stage already ran; its saved output was restored
        ctx = rt.ctx
        runner._record(ctx, stage, StageState.started)
        ctx._stage_tokens, ctx._stage_credits = 0, 0.0
        began = time.perf_counter()
        try:
            output = await runner._call_stage(stage, ctx, rt.slot)
        except StopRun as stop:
            runner._record(ctx, stage, StageState.done, stop.reason,
                           {**stop.payload, "stopped": stop.status.value, **runner._usage(ctx)},
                           runner._elapsed_ms(began))
            result: RunState = {"status": stop.status, "reason": stop.reason}
        except Exception as error:  # this run only; the batch carries on
            runner.log.exception("run %s failed in %s", ctx.run_id, stage.name)
            message = runner.plain_error(error)
            runner._record(ctx, stage, StageState.failed, message,
                           {"error": type(error).__name__, **runner._usage(ctx)}, runner._elapsed_ms(began))
            result = {"status": RunStatus.failed, "reason": f"{stage.label} failed: {message}"}
        else:
            ctx.outputs[stage.name] = output
            runner._record(ctx, stage, StageState.done, getattr(output, "message", ""),
                           {"output": output.model_dump(mode="json"), **runner._usage(ctx)},
                           runner._elapsed_ms(began))
            return {}
        for skipped in stages[index + 1:]:
            runner._record(ctx, skipped, StageState.skipped, "Not run")
        return result

    return node


async def _finish(state: RunState, config: RunnableConfig) -> RunState:
    """Every stage finished: the run ends with the status the stages left on the context."""
    ctx = _runtime(config).ctx
    return {"status": ctx.final_status, "reason": ctx.final_reason}


def _after(next_node: str):
    def route(state: RunState) -> str:
        return END if state.get("status") is not None else next_node

    return route


def build_graph(stages: list[Stage]):
    """Compile the graph for a list of stages (cheap; cached per list by the caller)."""
    graph = StateGraph(RunState)
    for i, stage in enumerate(stages):
        graph.add_node(stage.name, _stage_node(stages, i))
    graph.add_node("finish", _finish)
    graph.add_edge(START, stages[0].name)
    for i, stage in enumerate(stages):
        following = stages[i + 1].name if i + 1 < len(stages) else "finish"
        graph.add_conditional_edges(stage.name, _after(following), {following: following, END: END})
    graph.add_edge("finish", END)
    return graph.compile()


_last: tuple[list[Stage], Any] | None = None  # the stages it was built for (held, so identity is safe) + the graph


async def run_graph(stages: list[Stage], ctx: RunContext, slot: Any, start: int) -> RunState:
    """Run one prospect through the graph; returns the final status and reason."""
    global _last
    if _last is None or len(_last[0]) != len(stages) or any(a is not b for a, b in zip(_last[0], stages)):
        _last = (list(stages), build_graph(stages))  # the real pipeline never changes; tests swap it often
    return await _last[1].ainvoke({}, config={"configurable": {"runtime": Runtime(ctx, slot, start)}})
