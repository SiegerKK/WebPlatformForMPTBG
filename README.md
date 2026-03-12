# WebPlatformForMPTBG

A web platform for asynchronous turn-based multiplayer games. Provides a generic backend engine that game developers can build on top of via an SDK, and a React frontend for players to interact with those games.

---

## Architecture

The platform is organized into three layers:

| Layer | Description |
|-------|-------------|
| **Core** | FastAPI/PostgreSQL/Redis engine — manages matches, contexts, entities, events, turns, commands |
| **SDK** | Python classes that game developers extend to define game rules, entity archetypes, actions, and bot policies |
| **Games** | Concrete game implementations built with the SDK (e.g. Chess, Tic-Tac-Toe) |

```
backend/
├── app/           # Core engine (FastAPI routes, models, services)
├── sdk/           # Game developer SDK
│   ├── game_definition.py
│   ├── context_definition.py
│   ├── entity_archetype.py
│   ├── action_definition.py
│   ├── rule_set.py
│   ├── bot_policy.py
│   └── ui_schema.py
└── alembic/       # Database migrations

frontend/
└── src/
    ├── api/       # Axios API client
    ├── store/     # React context state management
    ├── types/     # TypeScript interfaces
    └── components/
```

---

## Key Concepts

- **Match** — A single game session with a unique seed, status, and set of participants.
- **Context** — A scoped game phase or sub-game within a match (e.g. setup phase, main phase). Contexts form a parent-child tree.
- **Entity** — An ECS-style game object with components (data), tags, and visibility rules.
- **Command** — A player intent submitted to the engine (e.g. "move unit to (3,4)"). Commands produce Events.
- **Event** — An immutable record of something that happened (e.g. "unit_moved"). Forms the game history.
- **Turn** — Tracks whose turn it is, submission status, deadlines, and turn modes (strict / simultaneous / async_window).

---

## Quick Start (Docker Compose)

### Prerequisites
- Docker and Docker Compose installed

### Run

```bash
git clone <repo-url>
cd WebPlatformForMPTBG
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs (Swagger): http://localhost:8000/docs

The backend runs `alembic upgrade head` automatically on startup.

---

## Backend API Overview

All endpoints are prefixed with `/api`.

| Resource | Endpoints |
|----------|-----------|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` |
| Matches | `GET/POST /matches`, `GET /matches/{id}`, `POST /matches/{id}/join`, `POST /matches/{id}/start` |
| Contexts | `POST /contexts`, `GET /contexts/{id}`, `GET /matches/{id}/contexts`, `GET /contexts/{id}/projection` |
| Entities | `GET /contexts/{id}/entities`, `POST /entities` |
| Commands | `POST /commands`, `GET /matches/{id}/commands` |
| Events | `GET /matches/{id}/events`, `GET /contexts/{id}/events` |
| Turns | `GET /contexts/{id}/turn`, `POST /contexts/{id}/turn/submit` |

Authentication uses JWT Bearer tokens (OAuth2 password flow).

---

## Frontend Overview

Built with **React 18 + TypeScript + Vite**.

- **Login/Register** — two-tab form; JWT stored in localStorage
- **Match List** — browse and create matches
- **Match View** — context tree, event log, entity cards, action list, tile grid
- **State** — React context + useReducer (no external state library)
- **API client** — Axios with automatic JWT injection

### Development (local, without Docker)

```bash
cd frontend
npm install
npm run dev   # starts Vite dev server on http://localhost:3000
```

The Vite dev server proxies `/api` requests to `http://backend:8000`.

---

## How to Create a New Game (SDK)

1. **Define entity archetypes** by subclassing `EntityArchetype`
2. **Define a context** by subclassing `ContextDefinition` — implement `get_available_actions` and `handle_command`
3. **Define the game** by subclassing `GameDefinition` — wire up contexts, turn modes, and win conditions
4. **Register the game** with the core engine's game registry

```python
from sdk import GameDefinition, ContextDefinition, EntityArchetype, ActionDefinition

class PawnArchetype(EntityArchetype):
    archetype = "pawn"

class MainContext(ContextDefinition):
    context_type = "main"

    def get_available_actions(self, state, player_id):
        return [ActionDefinition(type="move", label="Move Pawn")]

    def handle_command(self, state, command):
        # validate, mutate state, emit events
        ...

class MyGame(GameDefinition):
    game_id = "my_game"
    root_context = MainContext
```

---

## Development Setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start Postgres & Redis (or use docker compose up db redis)
export DATABASE_URL=postgresql://mptbg:mptbg_secret@localhost:5432/mptbg
export REDIS_URL=redis://localhost:6379/0
export SECRET_KEY=dev-secret

alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Run Tests

```bash
cd backend
pytest
```
