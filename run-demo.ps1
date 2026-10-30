# Start the backend in DEMO MODE on Windows (the same as `make dev-api-demo`).
#
# Replays the recorded demo: no internet, no API keys, no quota used. Use it with the
# "Use the demo CSV" button. It has its own database (backend/demo.db),
# so your real data is untouched. Stop the normal backend first (it also uses port 8000).
#
#   powershell -ExecutionPolicy Bypass -File run-demo.ps1

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "backend")

$env:DEMO_MODE = "fixtures"                 # answers come from tests/fixtures/demo
$env:FIXTURES_DIR = "tests/fixtures/demo"
$env:TODAY = "2026-10-19"                   # the day the demo was recorded
$env:RECENT_RUN_DAYS = "0"                  # re-running the demo is allowed
$env:DATABASE_URL = "sqlite:///demo.db"
$env:FIXTURE_DELAY_S = "1.5"                # stream stage by stage, like a live run
$env:PYTHONUTF8 = "1"

.\.venv\Scripts\python.exe -m scripts.seed_context
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
