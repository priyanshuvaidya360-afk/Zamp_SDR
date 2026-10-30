"""Replay the whole demo with the network off and check every row against seed/demo_cases.json.

  cd backend && .venv/bin/python -m scripts.check_demo

Runs the demo CSV in DEMO_MODE=fixtures (recordings in tests/fixtures/demo/) on a throwaway
database, then case 10's approve-after-twelve-days. Any connection to another host fails, so a
pass proves the demo needs no network and no API keys. Exit code 0 = every row as expected.
Spec: docs/PROJECT_SPEC.md T17 ("Done when: passes twice in a row with the network off").
"""

import asyncio
import json
import socket
import sys

from app.config import settings
from scripts import demo_common as demo


def block_network() -> None:
    real_connect = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError(f"network is off for check_demo (tried {host})")
        return real_connect(self, address)

    socket.socket.connect = connect
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError("network is off for check_demo"))


async def check() -> list[str]:
    settings.demo_mode = "fixtures"
    settings.fixtures_dir = demo.FIXTURES
    settings.sim_web_dir = None
    settings.fixture_delay_s = 0
    for key in ("groq_api_key", "gemini_api_key", "tavily_api_key", "exa_api_key"):
        if hasattr(settings, key):
            setattr(settings, key, "")  # replays must not need keys
    expected = json.loads(demo.CASES.read_text(encoding="utf-8"))
    result = await demo.run_demo()
    problems = []
    by_key = {(r["file"], r["row"]): r for r in result["rows"]}
    for case in expected["rows"]:
        got = by_key.get((case["file"], case["row"]))
        status = got["status"] if got else "missing"
        mark = "ok " if status == case["status"] else "BAD"
        print(f"{mark} {case['file']:16} {case['row']:>2} {case['name']:20} expected {case['status']:17} got {status:17} {case['case']}")
        if status != case["status"]:
            problems.append(f"{case['name']}: expected {case['status']}, got {status} ({got and got['reason']})")
        elif case.get("reason_contains") and case["reason_contains"] not in got["reason"]:
            problems.append(f"{case['name']}: reason '{got['reason']}' lacks '{case['reason_contains']}'")
        for key, want in (case.get("expect") or {}).items():
            have = got.get(key) if got else None
            if key == "hook_type_not":
                have_hook = got and got.get("hook_type")
                if have_hook in want:
                    problems.append(f"{case['name']}: hook is {have_hook}, which must not open")
            elif have != want:
                problems.append(f"{case['name']}: {key} expected {want!r}, got {have!r}")
    extra = set(by_key) - {(c["file"], c["row"]) for c in expected["rows"]}
    problems += [f"row not in demo_cases.json: {file} row {row}" for file, row in sorted(extra)]

    want10 = expected.get("case10")
    got10 = result["case10"] or {}
    if want10:
        ok = got10.get("outcome") == want10["outcome"] and got10.get("then", None) == want10.get("then")
        ok = ok and all(text in got10.get("export", "") for text in want10.get("export_contains", []))
        print(f"{'ok ' if ok else 'BAD'} case 10: approve after {demo.AGED_DAYS} days -> {got10.get('outcome')}, "
              f"then {got10.get('then')}; export has {want10.get('export_contains')}")
        if not ok:
            problems.append(f"case 10: {got10}")
    return problems


def main() -> int:
    block_network()
    problems = asyncio.run(check())
    print("\nPASS: every demo row as expected" if not problems else "\nFAIL:\n- " + "\n- ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
