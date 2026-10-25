"""The prompt loader (app/prompts.py) and the agent files it reads (agents/*.md)."""

import re

import pytest

from app import prompts

AGENTS = ["resolve", "research", "extract", "score", "account_plan", "draft", "recheck"]


def test_parse_sections_comments_and_notes():
    text = "# Title\nnotes\n\n## one\nfirst\n<!-- ignored -->\nsecond\n\n## two\nonly\n"
    assert prompts._parse(text) == {"one": "first\nsecond", "two": "only"}


def test_fill_is_strict_and_single_pass():
    assert prompts.fill("Hi {name}.", name="{other}") == "Hi {other}."  # values are never re-scanned
    with pytest.raises(KeyError, match="uses {missing}"):
        prompts.fill("Hi {missing}.", name="x")
    assert prompts.fill("a {{ b }} c") == "a {{ b }} c"  # only {lowercase_words} are placeholders


def test_unknown_section_and_agent_are_readable_errors():
    with pytest.raises(KeyError, match="no '## nope' section"):
        prompts.get("draft", "nope")
    with pytest.raises(FileNotFoundError, match="agents/ghost.md"):
        prompts.get("ghost", "system")


@pytest.mark.parametrize("agent", AGENTS)
def test_every_agent_file_loads_with_no_empty_section(agent):
    found = prompts.sections(agent)
    assert found and all(text for text in found.values()), agent
    assert any(name == "system" or name.endswith("_system") for name in found), f"{agent} has no system prompt"


def test_every_agent_is_listed_in_the_index():
    index = (prompts.AGENTS_DIR / "README.md").read_text(encoding="utf-8")
    assert all(prompts.file_name(agent) in index for agent in AGENTS)
    assert set(prompts.AGENT_FILES) == set(AGENTS)  # every agent has a descriptive file name


def test_placeholders_are_plain_words():
    for agent in AGENTS:
        for name, text in prompts.sections(agent).items():
            for word in re.findall(r"\{([^{}\n]*)\}", text):
                assert re.fullmatch(r"[a-z_][a-z0-9_]*", word), f"agents/{agent}.md ## {name}: odd placeholder {{{word}}}"
