.PHONY: setup up down restart logs status test migrate seed-admin dev build lint

# ── One-click setup (installs Docker if needed, builds & starts everything) ──
setup:
	bash setup.sh

# ── Docker Compose shortcuts ──────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

status:
	docker compose ps

build:
	docker compose build --parallel

# ── Development (bare metal, no Docker) ───────────────────────────────────────
dev:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Database ──────────────────────────────────────────────────────────────────
migrate:
	cd backend && alembic upgrade head

seed-admin:
	cd backend && python -m app.seed

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	cd backend && python -m pytest tests/ -x -q

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	cd backend && python -m flake8 app/ --max-line-length=120 --exclude=__pycache__ || true
	cd frontend && npm run lint || true
