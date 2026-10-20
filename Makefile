PYTHON ?= python3.15
VENV := backend/.venv

.PHONY: setup dev-api dev-web dev-api-demo test seed check-demo

setup:
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev-api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

# The recorded demo, no network or keys needed ("Use demo CSV" on the upload screen).
# FIXTURE_DELAY_S makes the replay stream like a live run.
# Its own database (demo.db, git-ignored) with the seed CRM data; repeat runs are allowed.
DEMO_ENV := DEMO_MODE=fixtures FIXTURES_DIR=tests/fixtures/demo TODAY=2026-10-19 RECENT_RUN_DAYS=0 \
	DATABASE_URL=sqlite:///demo.db
dev-api-demo:
	cd backend && $(DEMO_ENV) .venv/bin/python -m scripts.seed_context
	cd backend && $(DEMO_ENV) FIXTURE_DELAY_S=1.5 .venv/bin/uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/pytest -q

seed:
	cd backend && .venv/bin/python -m scripts.seed_context

check-demo:
	cd backend && .venv/bin/python -m scripts.check_demo
