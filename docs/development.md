# Development instructions

How to set up, run and validate APEX locally.

---

## Prerequisites

| Tool | Version used |
| --- | --- |
| Python | 3.11+ (3.14 verified) |
| Node.js | 20+ (24 verified) |
| Docker | 24+ with Compose v2 |
| Git | 2.40+ |

---

## First-time setup

```bash
git clone https://github.com/SiddharthS-Dev/apex.git
cd apex
cp .env.example .env
```

Open `.env` and replace every `change-me-in-your-local-env` placeholder.
`.env` is git-ignored and must never be committed.

For a value suitable for `APEX_JWT_SECRET`:

```bash
openssl rand -hex 32
```

---

## Running the stack

### Docker (all services)

```bash
docker compose up --build      # start
docker compose logs -f backend # follow one service
docker compose down            # stop
docker compose down -v         # stop and discard the database volume
```

### Backend only

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Serves on <http://localhost:8000>; interactive docs at `/docs` while
`APEX_DEBUG=true`.

### Frontend only

```bash
cd frontend
npm install
npm run dev
```

Serves on <http://localhost:5173>.

---

## Backend commands

Run from `backend/`. On macOS/Linux substitute `.venv/bin/python`.

| Command | Purpose |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | Run the test suite |
| `.venv/Scripts/python.exe -m ruff check .` | Lint |
| `.venv/Scripts/python.exe -m ruff check --fix .` | Lint and autofix |
| `.venv/Scripts/python.exe -m mypy app` | Strict type check |

---

## Frontend commands

Run from `frontend/`.

| Command | Purpose |
| --- | --- |
| `npm run dev` | Dev server with hot reload |
| `npm run typecheck` | TypeScript, no emit |
| `npm run lint` | ESLint |
| `npm test` | Vitest |
| `npm run build` | Typecheck and production build |
| `npm run preview` | Serve the production build locally |

---

## Configuration

All backend settings are read from `APEX_`-prefixed environment variables and
are declared in [`backend/app/core/config.py`](../backend/app/core/config.py).
Frontend settings use Vite's `VITE_` prefix.

`.env.example` is the canonical list. When you add a setting, add it there too
— with a safe placeholder, never a real value.

---

## The validation gate

Every commit must pass all of these before it is staged. This is the checklist
from the project's commit strategy, made concrete.

```bash
# 1. Review what changed
git status
git diff

# 2. Backend: tests, lint, types
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy app
cd ..

# 3. Frontend: types, lint, tests, build
cd frontend
npm run typecheck
npm run lint
npm test
npm run build
cd ..

# 4. Infrastructure
docker compose config --quiet

# 5. Database migrations (from Commit 003 onward)
# cd backend && .venv/Scripts/python.exe -m alembic upgrade head

# 6. Confirm no secrets are staged
git diff --cached --name-only
```

Then stage explicitly and commit:

```bash
git add <specific files>
git diff --cached
git commit -m "feat: <what this increment delivers>"
```

Never `git add .` without reviewing the staged diff first.

---

## Adding a domain

APEX is organised by bounded context. A new domain typically adds:

1. Models under `backend/app/<domain>/models.py`
2. An Alembic migration
3. Schemas, service layer and a router
4. The router mounted in `create_app()`
5. Tests under `backend/tests/`
6. Frontend views under `frontend/src/`
7. A line in `.env.example` if it introduces configuration

Keep domains isolated — cross-domain access goes through a service interface or
an integration event, not a direct table read.
