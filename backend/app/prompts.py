"""Prompt loader: every word a model reads lives in agents/*.md (one file per agent).

Format of an agent file:
  - text before the first `## ` heading is documentation for people and is ignored
  - each `## name` heading starts a section; its text (trimmed) is the prompt piece
  - `{placeholder}` marks a value the code fills in; a placeholder the code does not
    supply is an error, so a typo in a file fails loudly instead of reaching a model
  - a line that is only an HTML comment (<!-- like this -->) is ignored

Code picks which sections to use and fills in the facts (names, signals, numbers);
the words live here. Editing a file changes the prompt on the next run (no restart).
Recorded demo answers are matched by exact prompt text, so after editing a prompt re-record
(see CLAUDE.md "Commands") before using DEMO_MODE=fixtures.
"""

import re
from pathlib import Path
from typing import Any

from app.config import REPO_DIR

AGENTS_DIR = REPO_DIR / "agents"
_SECTION = re.compile(r"^## (\w+)[ \t]*$")
_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _parse(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if match := _SECTION.match(line):
            current = sections.setdefault(match.group(1), [])
        elif current is not None and not _COMMENT.match(line):
            current.append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def sections(agent: str) -> dict[str, str]:
    path = AGENTS_DIR / f"{agent}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file agents/{agent}.md is missing.")
    mtime = path.stat().st_mtime
    if agent not in _cache or _cache[agent][0] != mtime:
        _cache[agent] = (mtime, _parse(path.read_text()))
    return _cache[agent][1]


def get(agent: str, section: str, **values: Any) -> str:
    """The text of `## section` in agents/<agent>.md with {placeholders} filled in."""
    found = sections(agent)
    if section not in found:
        raise KeyError(f"agents/{agent}.md has no '## {section}' section (it has: {', '.join(found)}).")
    return fill(found[section], f"agents/{agent}.md ## {section}", **values)


def fill(template: str, where: str = "prompt", **values: Any) -> str:
    """Single pass: inserted values are never searched for placeholders themselves."""
    def put(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"{where} uses {{{name}}} but the code does not provide it.")
        return str(values[name])

    return _PLACEHOLDER.sub(put, template)
