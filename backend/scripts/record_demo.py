"""Record the demo: the demo CSV through the real pipeline, plus case 10's re-check.

  cd backend && .venv/bin/python -m scripts.record_demo

Real companies are researched on the live web (Tavily -> DuckDuckGo -> Exa); the fictional
companies in seed/simweb/ are served by the simulated web (app/simweb.py). Every search, page
read and model answer is saved to tests/fixtures/demo/, which scripts/check_demo.py replays.
Costs: roughly 40-60 Tavily credits and a few hundred thousand LLM tokens (free tiers).
After recording, review the statuses and update seed/demo_cases.json if a change is intended.
Resumable: run it again after a rate limit and only the missing calls are made (--fresh restarts).
"""

import argparse
import asyncio
import shutil
import tempfile
from pathlib import Path

from app import fixtures
from app.config import settings
from scripts import demo_common as demo


async def main(fresh: bool) -> None:
    if fresh:
        shutil.rmtree(demo.FIXTURES, ignore_errors=True)
    settings.demo_mode = "record"
    # Resumable: successful calls already recorded (here, or in the older recordings next door)
    # are reused; failed ones are tried again. Run again after a rate limit until no row fails.
    settings.record_reuse = True
    settings.record_reuse_dirs = [demo.FIXTURES.parent]
    # Free tiers: fewer runs at once, and wait a little longer on a 429 before falling back.
    settings.max_concurrent_runs = 2
    settings.llm_max_rate_limit_wait_s = 60
    settings.fixtures_dir = demo.FIXTURES
    settings.sim_web_dir = demo.SEED / "simweb"
    settings.sim_web_later = False
    # An empty cache: recordings keep each call's real credit cost (a cache hit records 0).
    settings.cache_dir = Path(tempfile.mkdtemp(prefix="signal-demo-cache-"))

    def twelve_days_later() -> None:
        settings.sim_web_later = True  # the fictional careers page now lists two roles

    result = await demo.run_demo(before_case10=twelve_days_later)
    for r in result["rows"]:
        print(f"{r['file']:16} row {r['row']:>2}  {r['name']:24} {r['status']:17} {r['reason'][:90]}")
    print("\ncase 10:", result["case10"] and {k: v for k, v in result["case10"].items() if k != "export"})
    if any(r["status"] == "failed" for r in result["rows"]):
        print("\nSome rows failed (usually a free-tier rate limit): run this again later to fill them in.")
        return
    # Replay once offline and keep only the recordings the demo actually uses (earlier passes
    # leave answers to prompts that have since changed).
    settings.demo_mode, settings.sim_web_dir, settings.sim_web_later = "fixtures", None, False
    fixtures.used.clear()
    await demo.run_demo()
    stale = [p for p in demo.FIXTURES.glob("*.json") if p.stem not in fixtures.used]
    for path in stale:
        path.unlink()
    print(f"\n{len(list(demo.FIXTURES.glob('*.json')))} fixtures in {demo.FIXTURES} ({len(stale)} unused removed)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fresh", action="store_true", help="delete the demo recordings and start over")
    asyncio.run(main(parser.parse_args().fresh))
