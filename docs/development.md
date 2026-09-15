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

## Database and migrations

The database URL is **not** stored in `alembic.ini`. Both the application and
Alembic read `APEX_DATABASE_URL`, so they cannot target different databases and
no credentials are committed.

```bash
cd backend
export APEX_DATABASE_URL="postgresql+psycopg://apex:<password>@localhost:5432/apex"
```

| Command | Purpose |
| --- | --- |
| `alembic current` | Show the applied revision |
| `alembic history` | Show the revision chain |
| `alembic upgrade head` | Apply all pending migrations |
| `alembic downgrade -1` | Roll back one revision |
| `alembic revision --autogenerate -m "add x"` | Generate a migration from model changes |

Prefix each with `.venv/Scripts/python.exe -m`.

### Writing a migration

1. Add or change models in the owning context.
2. **Import them in `app/core/registry.py`.** Autogenerate compares
   `Base.metadata` against the live database, and metadata only knows about
   models something has imported. A model nothing imports produces an empty
   migration and a table that is never created.
3. Autogenerate, then **read the generated file**. Autogenerate is a first
   draft: it misses data migrations, gets some type changes wrong, and will
   happily drop a column you meant to rename.
4. Verify the round trip — `upgrade head`, then `downgrade -1`, then
   `upgrade head` again. A migration that cannot be rolled back is a migration
   that cannot be deployed safely.

### Tenancy

APEX uses shared-schema multi-tenancy
([ADR-0008](adr/0008-shared-schema-multi-tenancy.md)). Two rules matter when
writing model and query code:

- **Inherit from `TenantScopedBase`** for tenant-owned tables, or `GlobalBase`
  for platform-level ones. Inheriting the tenant mixin is what opts a model
  into isolation.
- **Do not write `WHERE tenant_id = ...` by hand.** Wrap the operation in
  `tenant_scope(tenant_id)`; the session guards apply the filter to every ORM
  read and stamp every write. An unscoped read of tenant-owned data raises
  rather than returning everything.

`system_scope()` suspends filtering for genuinely cross-tenant platform work.
It must never wrap request handling.

### Object storage

MinIO provides S3-compatible storage locally. The backend reaches it through the
same `APEX_S3_*` settings that would point at AWS S3 in production — there is no
development-only code path in `app/storage/`.

| Setting | Local (MinIO) | AWS S3 |
| --- | --- | --- |
| `APEX_S3_ENDPOINT` | `http://localhost:9000` | leave empty |
| `APEX_S3_FORCE_PATH_STYLE` | `true` | `false` |
| `APEX_S3_ACCESS_KEY` / `_SECRET_KEY` | MinIO root credentials | IAM credentials |

Two rules matter when using the storage service:

- **Never pass a key.** `upload()` returns a `StoredObject` id; every later
  operation takes that id. The key is read from the row under tenant scope.
  This is what makes cross-tenant and arbitrary-path access inexpressible
  rather than merely forbidden.
- **Treat a signed URL as a credential.** It grants read access to one object
  for a few minutes. Do not log it, and do not store it.

Storage-backed tests need `APEX_TEST_S3_ENDPOINT`, `APEX_TEST_S3_ACCESS_KEY` and
`APEX_TEST_S3_SECRET_KEY`. Without them the MinIO tests skip and the in-memory
ones still run.

### Local port conflicts

Every published port is configurable, because a machine running other projects
will already have some of them taken. The symptoms are misleading: a foreign
PostgreSQL on `5432` produces `password authentication failed` rather than a
connection refusal, and a foreign MinIO on `9000` answers health checks quite
happily while storing nothing you can find.

```bash
POSTGRES_PORT=55432
APEX_S3_PORT=9300
APEX_S3_CONSOLE_PORT=9301
```

The container-to-container URL is unaffected — inside the Compose network the
backend always reaches `postgres:5432`.

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

# 5. Database migrations -- verify the round trip, not just the upgrade
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade base
.venv/Scripts/python.exe -m alembic upgrade head
cd ..

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
