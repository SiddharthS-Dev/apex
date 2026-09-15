============================================================
72A. GITHUB DEVELOPMENT AND COMMIT STRATEGY
============================================================

REPOSITORY:
https://github.com/SiddharthS-Dev/apex

DEFAULT BRANCH:
main

The APEX project MUST be developed incrementally and committed to
the GitHub repository one completed implementation step at a time.

Do NOT build the entire platform and create one massive commit.

Every meaningful implementation phase MUST result in its own Git commit.

============================================================
GITHUB RULES
============================================================

1. Treat the GitHub repository as the canonical source repository for
   the APEX implementation.

2. Work incrementally.

3. After completing each development step:
   - Run validation
   - Run tests
   - Run linting/type/static checks where applicable
   - Verify migrations
   - Verify the application builds
   - Review changed files
   - Commit the completed work

4. Commit only completed, coherent functionality.

5. Do not commit broken intermediate code merely to preserve progress.

6. Do not combine unrelated features into one commit.

7. Never commit:
   - .env
   - secrets
   - API keys
   - passwords
   - tokens
   - customer credentials
   - private certificates
   - generated sensitive data

8. Maintain a clean Git history.

9. Every commit must have a descriptive commit message.

10. Prefer Conventional Commit-style messages.

Examples:

feat: initialize apex architecture
feat: add database foundation
feat: implement identity and RBAC
feat: implement ontology domain
feat: implement asset management
feat: implement evidence and provenance
feat: implement governance workflow
feat: implement knowledge graph
feat: implement enterprise search
feat: implement integration event contracts
feat: implement KPI engine
feat: implement RAG pipeline
feat: implement agent orchestration
feat: implement apex vault
feat: implement apex academy

fix: correct asset lifecycle transition
fix: prevent unauthorized graph traversal

refactor: isolate governance domain
test: add asset lifecycle integration tests

docs: add apex architecture documentation
chore: configure CI pipeline

============================================================
COMMIT-BY-COMMIT DEVELOPMENT ORDER
============================================================

Commit 001
------------------------------------------------------------
"feat: initialize apex architecture"

Deliver:
- Repository structure
- README
- Architecture documentation
- .gitignore
- .env.example
- Docker foundation
- Development instructions
- Initial frontend/backend structure

Validation:
- Project structure verified
- Docker configuration validated
- Frontend starts
- Backend starts

Then COMMIT.

------------------------------------------------------------

Commit 002
------------------------------------------------------------
"docs: add apex system architecture"

Deliver:
- Context diagram
- Container diagram
- Component diagram
- Domain boundaries
- Authority boundaries
- Architecture decision records

Then COMMIT.

------------------------------------------------------------

Commit 003
------------------------------------------------------------
"feat: add database foundation"

Deliver:
- PostgreSQL configuration
- SQLAlchemy setup
- Alembic
- Database configuration
- Base model
- Migration framework
- Health check

Then COMMIT.

------------------------------------------------------------

Commit 004
------------------------------------------------------------
"feat: implement identity and RBAC"

Deliver:
- Users
- Roles
- Permissions
- User-role mapping
- Role-permission mapping
- Authentication
- JWT/OIDC-ready structure
- Authorization middleware

Then COMMIT.

------------------------------------------------------------

Commit 005
------------------------------------------------------------
"feat: implement ABAC policy foundation"

Deliver:
- Tenant
- Organization
- Audience
- Geography
- Data classification
- Policy evaluation foundation

Then COMMIT.

------------------------------------------------------------

Commit 006
------------------------------------------------------------
"feat: implement apex ontology"

Deliver:
- Domain
- Persona
- Problem
- Platform
- Application
- Device
- Sensor
- UseCase
- KPI
- ROI
- Owner
- Versioning
- Relationships

Then COMMIT.

------------------------------------------------------------

Commit 007
------------------------------------------------------------
"feat: implement asset management"

Deliver:
- Asset entity
- Asset versions
- Asset types
- Owners
- Stewards
- Audiences
- Metadata
- Hashing
- CRUD APIs
- Asset detail UI

Then COMMIT.

------------------------------------------------------------

Commit 008
------------------------------------------------------------
"feat: implement object storage"

Deliver:
- S3-compatible storage integration
- Upload
- Signed URLs
- Download controls
- Content hashing
- File metadata

Then COMMIT.

------------------------------------------------------------

Commit 009
------------------------------------------------------------
"feat: implement asset lifecycle"

Deliver:
- Draft
- Review
- Verified
- Approved
- Published
- Superseded
- Archived
- Rejected
- Expired

Implement state machine.

Then COMMIT.

------------------------------------------------------------

Commit 010
------------------------------------------------------------
"feat: implement evidence and claims"

Deliver:
- EvidenceReference
- Claims
- Authorities
- Confidence
- Validity
- Approval references
- Evidence APIs
- Evidence UI

Then COMMIT.

------------------------------------------------------------

Commit 011
------------------------------------------------------------
"feat: implement provenance engine"

Deliver:
- Source
- Provenance
- Version history
- Source tracing
- Evidence tracing
- Claim tracing
- Asset trace UI

Then COMMIT.

------------------------------------------------------------

Commit 012
------------------------------------------------------------
"feat: implement governance gates"

Deliver:

G0 Inventory
G1 Technical Verification
G2 Claim Verification
G3 Security and Rights
G4 Publication
G5 Performance Review
G6 Retention

Implement:
- Review queue
- Approval queue
- Rejection
- Escalation
- SLA
- Expiry
- Audit

Then COMMIT.

------------------------------------------------------------

Commit 013
------------------------------------------------------------
"feat: implement audit system"

Deliver:
- Immutable audit events
- Actor
- Resource
- Action
- Before/after
- Correlation ID
- IP/user agent
- Audit UI

Then COMMIT.

------------------------------------------------------------

Commit 014
------------------------------------------------------------
"feat: implement knowledge graph"

Deliver:
- Graph nodes
- Graph relationships
- Graph service
- Graph APIs
- Authorization-aware traversal
- Graph queries

Then COMMIT.

------------------------------------------------------------

Commit 015
------------------------------------------------------------
"feat: implement knowledge graph UI"

Deliver:
- Node explorer
- Relationship explorer
- Path tracing
- Evidence tracing
- Asset tracing
- Filters
- Graph visualization

Then COMMIT.

------------------------------------------------------------

Commit 016
------------------------------------------------------------
"feat: implement enterprise search"

Deliver:
- PostgreSQL/full-text search
- Metadata search
- Filters
- Pagination
- Search ranking
- Permission filtering

Then COMMIT.

------------------------------------------------------------

Commit 017
------------------------------------------------------------
"feat: implement vector search"

Deliver:
- pgvector
- Chunking
- Embeddings
- Embedding versioning
- Vector indexing
- Semantic retrieval

Then COMMIT.

------------------------------------------------------------

Commit 018
------------------------------------------------------------
"feat: implement hybrid search"

Deliver:

Keyword
+
Vector
+
Graph
+
Metadata
+
Evidence confidence
+
Freshness
+
Authorization

Then COMMIT.

------------------------------------------------------------

Commit 019
------------------------------------------------------------
"feat: implement ingestion pipeline"

Deliver:
- PDF
- DOCX
- PPTX
- XLSX
- CSV
- Images
- Text
- Validation
- Malware scanning
- Sensitive-data screening
- Prompt injection screening
- Extraction pipeline

Then COMMIT.

------------------------------------------------------------

Commit 020
------------------------------------------------------------
"feat: implement integration event contracts"

Deliver:
- Event schema
- Versioning
- Event IDs
- Correlation IDs
- Causation IDs
- Retry
- Idempotency
- Dead-letter strategy

Initial events:

release.evidence.approved
outcome.verified
asset.published
content.performance.updated
gate.updated
learning.asset.released
asset.expiry.due

Then COMMIT.

------------------------------------------------------------

Commit 021
------------------------------------------------------------
"feat: implement external integration adapters"

Deliver integration boundaries for:

IVEOS
Vanguard
CK/ESG-AIoT
Meris
Helix

Use references and events.

Do not duplicate authoritative records.

Then COMMIT.

------------------------------------------------------------

Commit 022
------------------------------------------------------------
"feat: implement KPI engine"

Deliver:
- KPI definitions
- KPI formulas
- KPI values
- Calculation timestamps
- Source references
- Targets
- Dashboard APIs

Then COMMIT.

------------------------------------------------------------

Commit 023
------------------------------------------------------------
"feat: implement apex command center"

Deliver:
- Knowledge readiness
- Approved assets
- Pending reviews
- Expiring assets
- Search success
- Reuse yield
- Commercial influence
- Learning adoption
- Traceable claims
- Trend readiness
- Integration health

No fake production metrics.

Then COMMIT.

------------------------------------------------------------

Commit 024
------------------------------------------------------------
"feat: implement trend radar"

Deliver:
- T01-T12
- Signal model
- Impact
- Confidence
- Urgency
- Weighted score
- Readiness
- Ownership
- Recommendations
- Trend UI

Then COMMIT.

------------------------------------------------------------

Commit 025
------------------------------------------------------------
"feat: implement grounded RAG"

Deliver:
- Query understanding
- Authorization
- Hybrid retrieval
- Candidate merge
- Re-ranking
- Evidence validation
- Context assembly
- Grounding validation
- Citations

Then COMMIT.

------------------------------------------------------------

Commit 026
------------------------------------------------------------
"feat: implement apex ai foundation"

Deliver:
- AI service
- Model registry
- Prompt registry
- Tool registry
- AI request tracking
- AI response tracking
- Citation handling

Then COMMIT.

------------------------------------------------------------

Commit 027
------------------------------------------------------------
"feat: implement apex agent orchestrator"

Deliver:
- Agent registry
- Agent execution
- Tools
- Policies
- Execution history
- Evaluation
- Human approval

Then COMMIT.

------------------------------------------------------------

Commit 028
------------------------------------------------------------
"feat: implement bounded apex agents"

Implement incrementally:

Curator Agent

then COMMIT

Evidence Verifier Agent

then COMMIT

Product Narrator Agent

then COMMIT

Learning Designer Agent

then COMMIT

Rights Controller Agent

then COMMIT

Commercial Packager Agent

then COMMIT

Research/Trend Agent

then COMMIT

Knowledge Graph Agent

then COMMIT

Executive Intelligence Agent

then COMMIT

Never combine all agent implementations into one unreviewed commit.

------------------------------------------------------------

COMMIT 029+
------------------------------------------------------------
Implement the ten APEX brands incrementally.

Recommended order:

APEX Vault
APEX Showcase
APEX Academy
APEX Playbooks
APEX Industry
APEX Partners
APEX Executive
APEX Studio
APEX Exchange
APEX AI

Each module should have its own coherent commit or small group of
coherent commits.

============================================================
GIT VALIDATION BEFORE EVERY COMMIT
============================================================

Before every commit execute:

1. Git status
2. Review changed files
3. Run tests
4. Run lint
5. Run static/type checks where applicable
6. Build frontend
7. Build backend
8. Validate database migrations
9. Verify no secrets are present
10. Verify no .env is staged
11. Verify no unintended files are staged

Only after all checks pass:

git add <specific-files>

git commit -m "<conventional commit message>"

============================================================
GIT COMMIT DISCIPLINE
============================================================

Never use:

git add .

without reviewing staged changes first.

Prefer:

git status
git diff
git diff --cached

Then explicitly stage intended files.

Every commit must have a clear purpose.

Avoid commits such as:

"updates"
"changes"
"work done"
"final"
"fix stuff"

Use meaningful messages.

============================================================
GIT BRANCH STRATEGY
============================================================

main:
Production-ready baseline.

For larger features use:

feature/<domain-name>

Examples:

feature/asset-management
feature/knowledge-graph
feature/rag
feature/apex-ai
feature/academy

Development flow:

feature branch
    ->
tests
    ->
commit
    ->
review
    ->
merge to main

Do not force-push main.

Never rewrite shared production history.

============================================================
CI/CD
============================================================

Each push or pull request should trigger:

1. Install dependencies
2. Lint
3. Unit tests
4. Integration tests
5. Security scan
6. Build frontend
7. Build backend
8. Migration validation
9. Docker build
10. Smoke tests

A feature is not considered complete merely because the code compiles.

============================================================
DEVELOPMENT CHECKPOINT
============================================================

After EACH commit, produce a concise development report:

Commit:
<hash>

Implemented:
<what was completed>

Files changed:
<summary>

Tests:
<result>

Build:
<result>

Database:
<result>

Security:
<result>

Remaining:
<next implementation step>

Then continue to the next phase.

============================================================
IMPORTANT
============================================================

Do not attempt to complete the entire APEX platform in one pass.

Develop APEX sequentially.

Implement.
Validate.
Commit.
Review.
Then continue.

The Git repository must show a clean progression of the enterprise
architecture from foundation -> knowledge -> governance -> intelligence
-> integrations -> AI -> APEX modules.

Every commit should represent a stable increment of the platform.
