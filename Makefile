PYTHON ?= python3.13
VENV := backend/.venv
# Windows venvs keep their programs in Scripts/, macOS and Linux in bin/.
BIN := $(if $(wildcard backend/.venv/Scripts/python.exe),Scripts,bin)

.PHONY: setup dev-api dev-web dev-api-demo test seed check-demo

setup:
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/$(BIN)/pip install -r backend/requirements.txt
	cd frontend && npm install

dev-api:
	cd backend && .venv/$(BIN)/uvicorn app.main:app --reload --port 8000

# The recorded demo, no network or keys needed ("Use demo CSV" on the upload screen).
# FIXTURE_DELAY_S makes the replay stream like a live run.
# Its own database (demo.db, git-ignored) with the seed CRM data; repeat runs are allowed.
DEMO_ENV := DEMO_MODE=fixtures FIXTURES_DIR=tests/fixtures/demo TODAY=2026-10-19 RECENT_RUN_DAYS=0 \
	DATABASE_URL=sqlite:///demo.db
dev-api-demo:
	cd backend && $(DEMO_ENV) .venv/$(BIN)/python -m scripts.seed_context
	cd backend && $(DEMO_ENV) FIXTURE_DELAY_S=1.5 .venv/$(BIN)/uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev

test:
	cd backend && .venv/$(BIN)/pytest -q

seed:
	cd backend && .venv/$(BIN)/python -m scripts.seed_context

check-demo:
	cd backend && .venv/$(BIN)/python -m scripts.check_demo
