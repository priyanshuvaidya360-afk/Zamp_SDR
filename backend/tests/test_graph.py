"""The LangGraph pipeline (app/pipeline/graph.py) and its diagram (agents/PIPELINE_DIAGRAM.md)."""

from app.pipeline.graph import build_graph
from app.pipeline.stages import STAGE_INFO, STAGES
from app.prompts import AGENTS_DIR
from scripts import draw_pipeline


def test_the_graph_is_the_stage_list_in_order_plus_finish():
    graph = build_graph(STAGES).get_graph()
    names = [s.name for s in STAGES]
    assert [n for n in graph.nodes if not n.startswith("__")] == names + ["finish"] or \
        set(graph.nodes) == {"__start__", "__end__", "finish", *names}
    forward = {(e.source, e.target) for e in graph.edges if not e.conditional or e.target != "__end__"}
    chain = ["__start__", *names, "finish", "__end__"]
    assert {(a, b) for a, b in zip(chain, chain[1:])} <= forward
    stops = {e.source for e in graph.edges if e.conditional and e.target == "__end__"}
    assert stops == set(names)  # every stage can end the run early


def test_every_stage_is_described_for_the_diagram():
    assert set(STAGE_INFO) == {s.name for s in STAGES}
    for info in STAGE_INFO.values():
        assert info["agent"] is None or (AGENTS_DIR / info["agent"]).exists()


def test_pipeline_md_is_up_to_date():
    assert (AGENTS_DIR / "PIPELINE_DIAGRAM.md").read_text(encoding="utf-8") == draw_pipeline.render(), \
        "agents/PIPELINE_DIAGRAM.md is out of date: run `python -m scripts.draw_pipeline` in backend/"
