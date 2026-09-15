# APEX

Enterprise knowledge, governance and intelligence platform.

APEX is the system of record for an organisation's knowledge assets and the
evidence behind them. It manages the full lifecycle of an asset — from draft,
through technical and claim verification, to publication, review and retention
— and keeps every published claim traceable back to its source.

The platform is built around a small number of load-bearing ideas:

| Idea | What it means in APEX |
| --- | --- |
| **Ontology** | A shared vocabulary — domains, personas, problems, platforms, applications, devices, sensors, use cases, KPIs — that everything else references. |
| **Evidence** | Claims are backed by evidence with a named authority, a confidence level and a validity window. Unevidenced claims do not publish. |
| **Provenance** | Every asset traces to its sources, versions and approvals. |
| **Governance** | Assets pass through gates (G0 inventory → G6 retention) with review queues, SLAs, escalation and an immutable audit trail. |
| **Authorization** | RBAC plus attribute-based policy (tenant, organisation, audience, geography, classification) filters search, graph traversal and retrieval. |
| **Grounded AI** | Retrieval and agents operate strictly inside the authorization and evidence boundaries, and cite what they use. |

> **Status:** early foundation. The platform is being built incrementally —
> see [Development roadmap](#development-roadmap).

---

## Technology

| Layer | Choice |
| --- | --- |
| Backend | Python 3.11+, FastAPI, Pydantic v2 |
| Persistence | PostgreSQL 16 (+ `pgvector` for semantic search) |
| Migrations | Alembic |
| Object storage | S3-compatible |
| Frontend | React 19, TypeScript, Vite |
| Containers | Docker, Docker Compose |

The reasoning behind these choices is recorded in
[ADR-0002](docs/adr/0002-technology-stack.md).

---

## Architecture

| Document | What it answers |
| --- | --- |
| [System context](docs/architecture/context.md) | Who uses APEX, and what it depends on |
| [Containers](docs/architecture/containers.md) | What is deployed, and how the pieces talk |
| [Components](docs/architecture/components.md) | What is inside the API service |
| [Domain boundaries](docs/architecture/domain-boundaries.md) | Which context owns what, and how to cross |
| [Authority boundaries](docs/architecture/authority-boundaries.md) | Which system or role may assert what |
| [Assumptions](docs/architecture/assumptions.md) | What was inferred, and what must be reconciled |
| [Decision records](docs/adr/README.md) | Why the load-bearing choices were made |

Four guarantees hold the design together — no read path can bypass
authorisation, no unevidenced claim can publish, no authoritative record is
duplicated, and no audit entry can be altered. Each is a structural property
rather than a review outcome; see the
[architecture index](docs/architecture/README.md).

---

## Quick start

### With Docker (recommended)

```bash
cp .env.example .env     # then edit the placeholder secrets
docker compose up --build
```

| Service | URL |
| --- | --- |
| Frontend | <http://localhost:5173> |
| Backend | <http://localhost:8000> |
| API docs | <http://localhost:8000/docs> |
| Health | <http://localhost:8000/health> |

### Without Docker

Backend:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Full instructions, including the validation gate, are in
[docs/development.md](docs/development.md).

---

## Repository layout

```
apex/
├── backend/            FastAPI service
│   ├── app/
│   │   ├── api/        HTTP routers
│   │   ├── core/       configuration, security, persistence
│   │   └── main.py     application factory
│   └── tests/
├── frontend/           React + TypeScript client
│   └── src/
├── docs/
│   ├── architecture/   system architecture
│   └── adr/            architecture decision records
└── docker-compose.yml  local development stack
```

---

## Validation gate

No commit lands until all of the following pass. See
[docs/development.md](docs/development.md) for the full checklist.

```bash
# Backend
cd backend && .venv/Scripts/python.exe -m pytest -q
cd backend && .venv/Scripts/python.exe -m ruff check .
cd backend && .venv/Scripts/python.exe -m mypy app

# Frontend
cd frontend && npm run typecheck && npm run lint && npm test && npm run build

# Infrastructure
docker compose config --quiet

# Database migrations
cd backend && .venv/Scripts/python.exe -m alembic upgrade head
```

Database-backed tests need a reachable PostgreSQL. Point
`APEX_TEST_DATABASE_URL` at a disposable database; without one those tests skip
rather than fail, so the suite stays runnable without Docker.

---

## Development roadmap

APEX is developed one coherent increment at a time. Each step is implemented,
validated and committed on its own; the platform is never built in a single
pass. The full commit-by-commit order, the pre-commit validation gate and the
branch strategy are defined in
[72A_GITHUB_DEVELOPMENT_AND_COMMIT_STRATEGY.md](72A_GITHUB_DEVELOPMENT_AND_COMMIT_STRATEGY.md).

Broad progression:

**foundation → knowledge → governance → intelligence → integrations → AI → APEX modules**

---

## Contributing

- Conventional Commit messages (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- Feature branches named `feature/<domain>`; `main` stays production-ready.
- Never commit `.env`, secrets, keys, tokens or credentials.
- Stage files explicitly after reviewing the diff — never `git add .` unreviewed.
