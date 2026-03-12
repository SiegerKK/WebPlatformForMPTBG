.PHONY: up down test migrate dev build lint

up:
	docker-compose up -d

down:
	docker-compose down

dev:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	cd backend && python -m pytest tests/ -x -q

migrate:
	cd backend && alembic upgrade head

build:
	docker-compose build

lint:
	cd backend && python -m flake8 app/ --max-line-length=120 --exclude=__pycache__ || true
	cd frontend && npm run lint || true
