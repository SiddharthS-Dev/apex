# ADR-0002 — Technology stack

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

The APEX development plan names several technologies directly — PostgreSQL,
SQLAlchemy, Alembic, `pgvector`, S3-compatible object storage, JWT/OIDC — and
requires that both a frontend and a backend build and start from the first
commit. Those constraints fix the persistence layer but leave the web
framework, the frontend stack and the container strategy open. Committing to
them now, before any domain code exists, is cheaper than discovering an
incompatibility at Commit 017.

Two requirements dominate the choice:

1. **Retrieval and authorization must be filtered together.** Hybrid search
   combines keyword, vector, graph and metadata signals, and the result must
   be filtered by the same access policy. If embeddings live in a separate
   vector database, permission filtering happens after retrieval — in
   application code, over a result set that was assembled without knowing who
   was asking. That is both slower and easier to get wrong.

2. **The evidence and governance model is relational.** Assets, versions,
   claims, authorities, gates and audit events are highly interconnected with
   strong integrity requirements. This is not a document-store shape.

## Decision

| Layer | Choice | Why |
| --- | --- | --- |
| Backend framework | **FastAPI** | Pydantic v2 models serve as validation, serialisation and OpenAPI schema in one definition. Native async suits the I/O-bound retrieval and agent workloads. Prescribed ORM (SQLAlchemy) integrates cleanly. |
| Data validation | **Pydantic v2** | Already the FastAPI contract layer; reused for settings. |
| Database | **PostgreSQL 16 + `pgvector`** | One store for relational data, full-text search and embeddings, so hybrid retrieval and permission filtering happen in a single query plane. |
| ORM / migrations | **SQLAlchemy + Alembic** | Prescribed by the development plan. Alembic gives reviewable, reversible schema changes. |
| Object storage | **S3-compatible** | Binaries do not belong in the database. Signed URLs let download control stay server-side. |
| Frontend | **React 19 + TypeScript + Vite** | Types across the API boundary; Vite keeps dev feedback fast and produces a static bundle nginx can serve. |
| Containers | **Docker + Compose** | One command reproduces the full stack; multi-stage builds separate dev and production targets. |

Python 3.11 is the floor (3.14 verified locally); Node 20 is the floor
(24 verified).

## Consequences

**Positive**

- A single database to operate, back up, migrate and reason about.
- Authorization-aware retrieval is expressible as a query rather than as
  post-filtering.
- OpenAPI is generated from the same models the API validates against, so the
  frontend contract cannot drift silently.
- The stack is mainstream; staffing and support are not a constraint.

**Negative**

- `pgvector` will not match a dedicated vector database at very large corpus
  sizes. Accepted deliberately: correctness of permission filtering matters
  more than peak retrieval throughput at this stage, and the retrieval layer
  is kept behind a service interface so it can be swapped if scale demands it.
- Python is slower than a compiled runtime for CPU-bound work. The workload is
  I/O-bound; embedding generation is offloaded regardless.
- Two toolchains (Python and Node) must be installed and kept current.

**Neutral**

- No server-side rendering. APEX is an authenticated internal platform, so SEO
  and first-paint-on-cold-cache are not drivers.
- No message broker yet. Integration events (Commit 020) will revisit this;
  the event contract is designed so the transport can change without touching
  producers or consumers.
