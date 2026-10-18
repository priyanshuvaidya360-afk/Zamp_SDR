PYTHON ?= python3.15
VENV := backend/.venv

.PHONY: setup dev-api dev-web test seed

setup:
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev-api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-web:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/pytest -q

seed:
	cd backend && .venv/bin/python -m scripts.seed_context
