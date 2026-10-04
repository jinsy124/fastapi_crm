# Implementation guide: phase by phase

Every phase = a goal, the steps, the files involved, a verification, and a commit message.
All code is already in this folder; read each file as you go, and re-type the parts you want to
own. The review call asks you to explain *why*, so each phase ends with "Be ready to explain".

> The code was written but **not run** by whoever generated it (no PostgreSQL there).
> Phase 8 is where you find out what needs fixing. Expect a few small fixes.

---

## Phase 1: Project setup  (about 1 hour)

**Steps**
1. `mkdir crm-leads-api && cd crm-leads-api && git init`, then create the public GitHub repo and push.
2. Create the folders from the brief's layout, with an `__init__.py` in each package.
3. Add `requirements.txt`, `.gitignore` (must list `.env`), `.env.example`, `pytest.ini`.
4. Write `src/config.py` (pydantic-settings). It fails at startup if `DATABASE_URL` or `JWT_SECRET` is missing.
5. Write `src/db/base.py`, `src/db/types.py`, `src/db/session.py`.
6. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

**Files:** `requirements.txt`, `.env.example`, `.gitignore`, `pytest.ini`, `alembic.ini`,
`src/config.py`, `src/db/{base,types,session}.py`

**Verify:** `python -c "from src.config import settings; print(settings.database_url)"` works with your `.env`.

**Commits:** `Add project skeleton and config` · `Add async engine and session dependency`

**Be ready to explain**
- Why `expire_on_commit=False` (async sessions can't lazy-load after commit).
- Why `str_enum` uses VARCHAR + CHECK instead of native PG enums.

---

## Phase 2: Data model + first migration  (about 2 hours): **day-1 check-in material**

**Steps**
1. `src/core/enums.py`: `Role`, `LeadSource`, `LeadStatus`, `FollowUpType`.
2. Models: `users/models.py`, `leads/models.py` (Lead + LeadStatusHistory), `follow_ups/models.py`.
3. Name your constraints explicitly (`uq_users_email`, `uq_leads_phone`). You'll match on those names later.
4. `alembic.ini` and `migrations/env.py` (async version, imports every model module).
5. Write the migration. Ideally generate it first:
   `alembic revision --autogenerate -m "initial schema"`, compare with `migrations/versions/0001_initial_schema.py`, and fix anything autogenerate got wrong.
6. Test on an empty DB: `alembic upgrade head`, then `alembic downgrade base`, then `alembic upgrade head`.
7. Prepare a one-page ER sketch for the check-in.

**Files:** `src/core/enums.py`, `src/*/models.py`, `migrations/env.py`, `migrations/versions/0001_*.py`

**Verify:** `psql crm_leads -c '\d leads'` shows the unique constraint, the 3 indexes, 2 foreign keys and the CHECK constraints.

**Commits:** `Add enums` · `Add users model` · `Add leads and status history models` · `Add follow-ups model` · `Add Alembic and initial migration`

**Be ready to explain**
- Why `leads.assigned_to_id` is the Python name for the DB column `assigned_to` (so the relationship can be called `assigned_to` and nest in responses).
- Why timestamps are `DateTime(timezone=True)` and why there's both a Python `default` and a `server_default`.
- Why `phone` is `String(16)` (a `+` plus 15 digits).
- Which indexes exist and which query each one serves.

---

## Phase 3: Core layer  (about 2 hours)

**Steps**
1. `core/errors.py`: `AppError(message, code, details, headers)` plus one subclass per situation.
2. `core/handlers.py`: handlers for `AppError`, `RequestValidationError`, Starlette's `HTTPException` (unknown routes), and a catch-all `Exception` that logs and returns a generic 500.
3. `core/security.py`: Argon2 via `pwdlib`, wrapped in `asyncio.to_thread`. Also `DUMMY_HASH`.
4. `core/jwt.py`: tokens with `sub`, `type`, `exp`. `decode_token(token, expected_type)`.
5. `core/deps.py`: `get_current_user` and `require_roles(...)`.

**Files:** `src/core/{errors,handlers,security,jwt,deps,schemas}.py`

**Verify:** a quick REPL check: `create_access_token(uuid4())` then `decode_token(tok, "access")` works, and `decode_token(tok, "refresh")` raises `Unauthorized`.

**Commits:** `Add custom error classes and global handlers` · `Add password hashing off the event loop` · `Add JWT helpers` · `Add current-user and role dependencies`

**Be ready to explain**
- Why `HTTPBearer(auto_error=False)` (default gives 403 for a missing token; the brief wants 401).
- Why `get_current_user` loads the user from the DB on every request (that is how deactivation takes effect immediately).
- Why password hashing is moved to a thread.
- Why `algorithms=[...]` is passed explicitly to `jwt.decode`.
- Why services raise `AppError` rather than `HTTPException` (business code stays framework-free; one place builds the response).

---

## Phase 4: Auth + users + seed  (about 2 hours)

**Steps**
1. `users/schemas.py`: `UserCreate` (lowercases email), `UserOut` (no `password_hash`), `UserBrief`.
2. `users/service.py`: `create_user` (catch `IntegrityError` and map to 409), `list_users`, `set_active`.
3. `users/routes.py`: `POST /users` (admin), `GET /users` (admin+manager), `PATCH /users/{id}/active` (admin).
4. `auth/service.py` + `routes.py`: login (same 401 for every failure), refresh, me.
5. `main.py`: create the app, register handlers, include routers under `/api/v1`.
6. `scripts/seed.py`: admin from env, idempotent.
7. Run it: `alembic upgrade head && python -m scripts.seed && uvicorn src.main:app --reload`, then log in via `/docs`.

**Files:** `src/users/*`, `src/auth/*`, `src/main.py`, `scripts/seed.py`

**Verify:** log in as the seeded admin; `GET /auth/me`; create a manager; call `POST /users` as that manager and get 403.

**Commits:** `Add user schemas and service` · `Add login, refresh and me endpoints` · `Add user creation and listing` · `Add seed script` · `Allow admin to deactivate users`

**Be ready to explain**
- Why the unique constraint, not a pre-check, is what stops duplicate emails.
- Why login verifies against `DUMMY_HASH` when the user doesn't exist.
- Why the refresh endpoint re-checks `is_active`.

---

## Phase 5: Leads  (about 2.5 hours)

**Steps**
1. `leads/schemas.py`: `normalise_phone`, `LeadCreate` (phone validator), `LeadOut`, `LeadList`.
2. `leads/service.py`:
   - `get_lead_for_user`: **the** visibility rule. Staff get `WHERE assigned_to = me`, so "not yours" and "doesn't exist" are both `NotFound`.
   - `create_lead`: staff auto-assign / 403 on `assigned_to`; insert; catch `IntegrityError` for `uq_leads_phone`.
   - `list_leads`: filters, escaped `ILIKE` search, separate `COUNT`, newest first.
   - `assign_lead` + `_valid_assignee`.
3. `leads/routes.py`: `POST /leads`, `GET /leads`, `GET /leads/{id}`, `PATCH /leads/{id}/assignee`.
4. Try the duplicate-phone case by hand: `98470 12345` then `9847012345`.

**Files:** `src/leads/{schemas,service,routes}.py`

**Verify:** the "Leads" and "Assignment" sections of the acceptance checklist.

**Commits:** `Add phone normalisation` · `Add lead creation with duplicate phone handling` · `Add lead visibility rule for staff` · `Add lead listing with filters and pagination` · `Add lead assignment`

**Be ready to explain**
- Why phone normalisation lives in a schema validator (bad phones become 422 before reaching the service).
- Why `with_for_update(of=Lead)` and `populate_existing=True` appear.
- Why `_reload` re-queries the lead after commit (fresh nested assignee, fresh `updated_at`).
- Why `model_fields_set` is used to detect "staff sent `assigned_to`".
- Why `%` and `_` are escaped in search.

---

## Phase 6: Status transitions  (about 1.5 hours)

**Steps**
1. Put the whole pipeline in one dict, `TRANSITIONS`, plus `REOPEN_ROLES`.
2. `change_status` in this order:
   1. load the lead with `FOR UPDATE` (404 if not visible)
   2. `lost` needs a `reason` → 422
   3. target must be in `TRANSITIONS[current]` → 409 (message lists the allowed statuses)
   4. `lost → contacted` only for admin/manager → 403
   5. add the history row, set the new status, **one** `commit()`
3. `GET /leads/{id}/status-history`, oldest first.
4. Walk the pipeline by hand in Swagger: `new → contacted → qualified → proposal_sent → won`, then try to leave `won`.

**Files:** `src/leads/service.py` (status section), `src/leads/routes.py`

**Commits:** `Define lead status transition table` · `Reject invalid status transitions` · `Record status history in the same transaction` · `Restrict reopening lost leads to managers and admins`

**Be ready to explain**
- Why history and status update share one commit (one transaction, so both are saved or neither).
- What the row lock prevents (two simultaneous changes both passing the check).
- Why the order is 404 → 422 → 409 → 403.
- Be ready for a live change such as "add a `on_hold` status": that means the enum, the CHECK constraint (a migration), and `TRANSITIONS`.

---

## Phase 7: Follow-ups  (about 1 hour)

**Steps**
1. `follow_ups/schemas.py`: `scheduled_at` validator rejects naive datetimes and converts to UTC.
2. `add_follow_up`: lock the lead; 404 → past-time 422 → closed-lead 409; `created_by` from the token.
3. `list_follow_ups`: soonest first.
4. `complete_follow_up`: join to the lead for the staff-ownership rule; lock the row; 409 if already done; server sets `completed_at`.
5. Wire the router into `main.py`.

**Files:** `src/follow_ups/*`

**Commits:** `Add follow-up creation` · `Block follow-ups on won and lost leads` · `Add follow-up completion`

**Be ready to explain**
- Why `complete` goes through the parent lead for visibility (a follow-up has no owner of its own).
- Why `outcome=None, completed_at=None` are set explicitly on insert (unloaded attributes would try to lazy-load in async code).

---

## Phase 8: Tests  (about 2 hours)

**Steps**
1. Create the test DB: `createdb crm_leads_test`; set `TEST_DATABASE_URL` in `.env`.
2. `tests/conftest.py`: points the app at the test DB, runs the Alembic migrations once per session, truncates tables after each test, provides `client`, user fixtures and `auth(user)`.
3. `tests/test_required.py`: the five required tests.
4. `tests/test_extras.py`: the rest of the checklist, including 5 simultaneous identical `POST /leads`.
5. Run `pytest -x -q`. Fix failures one at a time. **This is where the untested code gets debugged.**

**Commits:** `Add test fixtures and migrated test database` · `Add required tests` · `Add auth and deactivation tests` · `Add concurrency test for duplicate phone`

**Likely first-run snags**
- `MissingGreenlet`: something is being lazy-loaded. Find the attribute and eager-load it or reload the object.
- Event loop errors: check `pytest.ini` has all three `asyncio_*` settings and your `pytest-asyncio` is >= 0.24.
- Argon2 import error: `pip install "pwdlib[argon2]"`.

**Be ready to explain**
- Why the tests use the real Alembic migrations, not `create_all`.
- Why the test DB name must end in `_test`.
- Why `raise_app_exceptions=False` on the test transport.
- Why the concurrency test would fail with a "check then insert" implementation.

---

## Phase 9: README, checklist, polish  (about 1 hour)

**Steps**
1. Delete the database, then follow your own README from a **fresh clone**: venv → install → `.env` → migrate → seed → run → login.
2. Work through the brief's section 9 checklist in Swagger. Tick each item.
3. Read every row in the README "Decisions" table and make sure it's true of *your* final code. Change what isn't.
4. Fill in **Known gaps** honestly.
5. `git log --oneline` should read as a story of small commits. Final check: `git ls-files | grep -E '^\.env$'` prints nothing.

**Commits:** `Write README setup and decisions` · `Document known gaps`

---

## Review-call cheat sheet

| They might ask | Where to look |
|---|---|
| How do you stop two identical requests creating two leads? | `uq_leads_phone` + `IntegrityError` handler in `leads/service.py` |
| How does a deactivated user get locked out? | `get_current_user` in `core/deps.py` |
| Why 404 and not 403 for staff? | `get_lead_for_user` in `leads/service.py` |
| Where are the status rules? | `TRANSITIONS` and `change_status` in `leads/service.py` |
| Why is history consistent with status? | One `session.commit()` in `change_status` |
| What shape do errors have, and who builds it? | `core/errors.py` and `core/handlers.py` |
| Where are role checks? | `require_roles` in `core/deps.py`, used in the route signatures |
| What happens on an unexpected exception? | `handle_unexpected`: logs the traceback, returns a generic 500 |


Step 0: Prerequisites and project setup
Goal: an empty project managed by uv, PostgreSQL running in Docker, and the first commit pushed to GitHub.
Install first: Python 3.11 or newer, Docker Desktop, Git, a GitHub account, and uv (Herbally's package tool):
brew install uv          # or: curl -LsSf https://astral.sh/uv/install.sh | sh
0.1 Create the folder
mkdir crm-leads-api && cd crm-leads-api
git init
0.2 pyproject.toml
Why pyproject instead of requirements.txt: one file holds the dependencies and the tool settings (pytest here). uv reads it, creates .venv, and pins exact versions in uv.lock so every machine installs the same thing. Commit uv.lock.
[project]
name = "crm-leads-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic-settings>=2.4",
    "email-validator>=2.1",
    "pyjwt>=2.9",
    "pwdlib[argon2]>=0.2",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
]

[tool.uv]
package = false   # an application, not a library to publish

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
pythonpath = ["."]
testpaths = ["tests"]
uv sync                      # creates .venv and installs everything, dev group included
source .venv/bin/activate    # do this in every new terminal
Package
Why we need it
fastapi, uvicorn
The web framework and the server that runs it
sqlalchemy[asyncio], asyncpg
Talk to PostgreSQL without blocking (async)
alembic
Create and change tables through migrations
pydantic-settings
Read settings from .env
email-validator
Lets Pydantic's EmailStr check emails
pyjwt
Create and read JWT tokens
pwdlib[argon2]
Hash passwords with Argon2
pytest, pytest-asyncio, httpx
Run async tests against the app
0.3 docker-compose.yml
Herbally rule: networks and volumes are prefixed with the project name, and ports come from env vars with defaults.
services:
  postgres:
    image: postgres:16-alpine
    container_name: crm_leads_postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-crm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-crm}
      POSTGRES_DB: ${POSTGRES_DB:-crm}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - crm_leads_postgres_data:/var/lib/postgresql/data
    networks:
      - crm_leads_network

networks:
  crm_leads_network:
    name: crm_leads_network

volumes:
  crm_leads_postgres_data:
    name: crm_leads_postgres_data
docker compose up -d
# a second, empty database just for tests
docker compose exec postgres createdb -U crm crm_test
0.4 .env.example (committed) and .env (never committed)
Herbally rule: every variable is documented, grouped by section, with an inline comment.
# ==================== App Settings ====================
APP_NAME=crm_leads_api                  # Used for log file names
DEBUG=false                             # true = DEBUG-level logs
API_V1_PREFIX=/api/v1                   # Mount point for all v1 routes
CORS_ORIGINS=["http://localhost:5173"]  # Frontend origins allowed to call the API (JSON list)

# ==================== Database Settings ====================
DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm            # Main database
TEST_DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm_test  # Wiped by the tests

# ==================== JWT Settings ====================
JWT_SECRET_KEY=replace-me-with-at-least-32-random-characters  # Signs tokens; min 32 chars
JWT_ALGORITHM=HS256                     # Signing algorithm
ACCESS_TOKEN_EXPIRE_MINUTES=15          # Access token lifetime
REFRESH_TOKEN_EXPIRE_DAYS=7             # Refresh token lifetime

# ==================== Lead Settings ====================
DEFAULT_COUNTRY_CODE=91                 # Added in front of 10-digit phone numbers

# ==================== Seed Settings ====================
ADMIN_EMAIL=admin@example.com           # First admin, created by the seed
ADMIN_PASSWORD=change-me-please         # Their password
ADMIN_FULL_NAME=Admin                   # Their display name
DEMO_PASSWORD=                          # Optional: also seed a demo manager + staff
cp .env.example .env
# make a real secret and paste it into .env as JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"
0.5 .gitignore
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
logs/
.DS_Store
0.6 Empty package folders
mkdir -p app/core/crud app/apis app/user/auth_management app/lead app/follow_up scripts tests logs
touch app/__init__.py app/core/__init__.py app/apis/__init__.py \
      app/user/__init__.py app/user/auth_management/__init__.py \
      app/lead/__init__.py app/follow_up/__init__.py tests/__init__.py


CRM Leads API — Build-It-Yourself Study Guide
4 Oct 2026 · @Jinsy HIC
Overview: what we're building and how to use this guide
You will build crm-leads-api in 11 steps, following the Herbally backend conventions (see the next section). Each step adds a few files, ends with something you can run or check, and finishes with one git commit. Type every file yourself. Don't copy and paste. Typing it is how you learn it, and in the review call you will have to explain every line.
What the API does: staff capture leads, managers assign them, staff log follow-ups, and each lead moves through a fixed status pipeline. Everything is about rules: who may do what, which status moves are valid, and what happens on bad input or two requests at the same moment.
The layers every request passes through (strict: never skip one)
Layer
File
Its only job
Example
Route
app/<module>/routes.py
Inject dependencies, read the request, call one service method, return the result
POST /leads calls lead_service.create_lead(...)
Dependency
app/user/dependencies.py
Checks FastAPI runs before the route: current user, role guard
Depends(require_roles(*CAN_ASSIGN_LEADS))
Service
app/<module>/service.py
Business rules + the transaction: the only layer that calls session.commit()
"Is this the staff member's own lead?"
CRUD
app/<module>/crud.py
Database reads and writes. flush() + refresh(), never commit(). No business rules
lead_crud.get_list_filtered(...)
Model
app/<module>/models.py
Tables as Python classes. No logic
Lead, User
Schemas (schemas.py) sit at the edge: request schemas (LeadCreate) check what comes in, response schemas (LeadResponse) control what goes out, so password_hash can never leak.
Final project layout
crm-leads-api/
├── app/
│   ├── __init__.py
│   ├── core/                        # reusable: names no business concept
│   │   ├── main.py                  # create_app() + lifespan
│   │   ├── settings.py              # Settings (UPPER_CASE fields)
│   │   ├── database.py              # engine + get_session
│   │   ├── models.py                # Base, enum_values()
│   │   ├── schemas.py               # ListParams, SortOrder
│   │   ├── exceptions.py            # AppException family + global handlers
│   │   ├── middleware.py            # CORS, GZip, timing header
│   │   ├── logging.py               # setup_logging(), get_logger()
│   │   ├── utils.py                 # utc_now()
│   │   ├── alembic_models_import.py # every model, for Alembic
│   │   └── crud/
│   │       ├── __init__.py
│   │       ├── base.py              # CRUDBase
│   │       └── helpers.py           # apply_sorting(), paginated_select()
│   ├── apis/
│   │   ├── __init__.py
│   │   └── v1.py                    # router registry
│   ├── user/
│   │   ├── __init__.py              # public API: User, auth_router, user_router
│   │   ├── enums.py  models.py  exceptions.py  permissions.py
│   │   ├── schemas.py  crud.py  service.py  routes.py
│   │   ├── dependencies.py          # get_current_active_user, require_roles
│   │   ├── seed.py                  # idempotent: python -m app.user.seed
│   │   └── auth_management/
│   │       ├── __init__.py
│   │       ├── security.py          # password hashing + JWT
│   │       └── schemas.py  service.py  routes.py
│   ├── lead/
│   │   ├── __init__.py  enums.py  models.py  exceptions.py  permissions.py
│   │   ├── phone.py  status_rules.py
│   │   └── schemas.py  crud.py  service.py  routes.py
│   └── follow_up/
│       ├── __init__.py  enums.py  models.py  exceptions.py
│       └── schemas.py  crud.py  service.py  routes.py
├── migrations/                      # env.py, script.py.mako, versions/
├── scripts/seed.py                  # thin wrapper the brief asks for
├── tests/
├── logs/                            # gitignored
├── alembic.ini
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md
The placement rule that decides everything: a file's folder is decided by what it is, not what it is called. core/ holds only things every project could reuse. Anything that knows about users, roles, leads or follow-ups lives in that module, never in core/.
How to study each step
1. Read the "why" paragraph before you type.
2. Type the code. Change names only once you understand them.
3. Run the check at the end of the step. If it fails, read the error top to bottom before asking for help.
4. Commit with the message given.
5. Close the guide and explain the step out loud in 3 sentences. If you can't, re-read it.
Day plan: Day 1 = Steps 0 to 5 (setup, core, users, auth, first migration: this is your check-in). Day 2 = Steps 6 to 11 (leads, status, follow-ups, tests, README).
Herbally conventions: what this project adopts, adapts or skips
The guide now follows the Herbally backend conventions, with one rule on top: when a convention and the trainee brief disagree, the brief wins, because the brief is what gets graded. Every such case is listed under "Decisions" in the README (Step 11).
Convention
Verdict
What this project does
app/ layout: core/, apis/v1.py, one folder per feature (user/, lead/, follow_up/)
Adopt
Replaces the brief's suggested src/ layout. The brief allows this: it says "suggested".
Route → Service → CRUD → Model; the service commits, CRUD only flushes
Adopt
Each module gets a crud.py. A generic CRUDBase lives in app/core/crud/.
Naming: UserResponse, user_crud, lead_service, lead_router, UPPER_SNAKE settings
Adopt
Response schemas end in Response; services and CRUD are module-level singletons.
ListParams + paginated_select() (one query with COUNT(*) OVER ()) + id tiebreaker
Adopt, adapted
Query params stay limit / offset, max 100, because the brief fixes that contract. Sorting is added through LeadSortField.
Settings: UPPER_CASE fields, section comments, @lru_cache, secrets at least 32 chars
Adopt
app/core/settings.py
Models: explicit UUID(as_uuid=True), DateTime(timezone=True), nullable=, named indexes in __table_args__, default enum type names
Adopt
We also name the two unique constraints, because the service needs uq_leads_phone to return duplicate_phone.
Soft delete columns (is_deleted, deleted_at)
Skip
The brief puts deleting leads out of scope.
Timestamped migration file names, alembic_models_import.py
Adopt
Set in alembic.ini; env.py star-imports the import file.
Module exceptions.py as HTTPException subclasses
Adapt
The brief forbids HTTPException in services. Module exceptions subclass our own AppException from app/core/exceptions.py.
Global handlers: IntegrityError 409, OperationalError 503, ValueError 400, PermissionError 403, catch-all 500
Adopt
All of them return the brief's {"error": {code, message, details}} shape.
Permission RBAC: require_permission("crm_leads:read") with Role/Permission tables
Adapt
The brief fixes three roles in users.role. We keep require_roles(...), but each module's role sets live in its own permissions.py, and the dependency lives in app/user/dependencies.py (auth is user-module, never core/).
Scope layer: CRUD filters by owner_user_ids
Adapt
Same CRUD signature, but the service computes the scope, because the brief says "is this the user's own lead?" belongs in the service.
Idempotent seed in app/user/seed.py, run with python -m app.user.seed
Adopt, plus
scripts/seed.py stays as a thin wrapper, because the brief requires that path. It does not run at startup.
get_logger(), rotating logs/<APP_NAME>.log and _errors.log
Adopt
Console colours skipped.
Middleware (CORS, GZip, timing)
Adopt, minimal
CORS is needed for the frontend later.
Module __init__.py public API
Adopt
Only apis/v1.py imports from it. Inside the project, import from submodules to avoid circular imports.
Celery, Redis cache, S3 storage, Prometheus metrics, Flower
Skip
Nothing in the brief needs them; notifications and uploads are out of scope.
pyproject.toml + uv; Docker names prefixed with the project; postgres:16-alpine
Adopt
API Dockerfile is left as the brief's optional extra.
Dependency style session: AsyncSession = Depends(get_session), get_current_active_user
Adopt
Used in every route.
Enums as class X(str, Enum) with UPPER members
Adopt
The database and API still use lowercase values ("new", "admin") via values_callable. In messages always write .value.
Step 0: Prerequisites and project setup
Goal: an empty project managed by uv, PostgreSQL running in Docker, and the first commit pushed to GitHub.
Install first: Python 3.11 or newer, Docker Desktop, Git, a GitHub account, and uv (Herbally's package tool):
brew install uv          # or: curl -LsSf https://astral.sh/uv/install.sh | sh
0.1 Create the folder
mkdir crm-leads-api && cd crm-leads-api
git init
0.2 pyproject.toml
Why pyproject instead of requirements.txt: one file holds the dependencies and the tool settings (pytest here). uv reads it, creates .venv, and pins exact versions in uv.lock so every machine installs the same thing. Commit uv.lock.
[project]
name = "crm-leads-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic-settings>=2.4",
    "email-validator>=2.1",
    "pyjwt>=2.9",
    "pwdlib[argon2]>=0.2",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
]

[tool.uv]
package = false   # an application, not a library to publish

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
pythonpath = ["."]
testpaths = ["tests"]
uv sync                      # creates .venv and installs everything, dev group included
source .venv/bin/activate    # do this in every new terminal
Package
Why we need it
fastapi, uvicorn
The web framework and the server that runs it
sqlalchemy[asyncio], asyncpg
Talk to PostgreSQL without blocking (async)
alembic
Create and change tables through migrations
pydantic-settings
Read settings from .env
email-validator
Lets Pydantic's EmailStr check emails
pyjwt
Create and read JWT tokens
pwdlib[argon2]
Hash passwords with Argon2
pytest, pytest-asyncio, httpx
Run async tests against the app
0.3 docker-compose.yml
Herbally rule: networks and volumes are prefixed with the project name, and ports come from env vars with defaults.
services:
  postgres:
    image: postgres:16-alpine
    container_name: crm_leads_postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-crm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-crm}
      POSTGRES_DB: ${POSTGRES_DB:-crm}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - crm_leads_postgres_data:/var/lib/postgresql/data
    networks:
      - crm_leads_network

networks:
  crm_leads_network:
    name: crm_leads_network

volumes:
  crm_leads_postgres_data:
    name: crm_leads_postgres_data
docker compose up -d
# a second, empty database just for tests
docker compose exec postgres createdb -U crm crm_test
0.4 .env.example (committed) and .env (never committed)
Herbally rule: every variable is documented, grouped by section, with an inline comment.
# ==================== App Settings ====================
APP_NAME=crm_leads_api                  # Used for log file names
DEBUG=false                             # true = DEBUG-level logs
API_V1_PREFIX=/api/v1                   # Mount point for all v1 routes
CORS_ORIGINS=["http://localhost:5173"]  # Frontend origins allowed to call the API (JSON list)

# ==================== Database Settings ====================
DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm            # Main database
TEST_DATABASE_URL=postgresql+asyncpg://crm:crm@localhost:5432/crm_test  # Wiped by the tests

# ==================== JWT Settings ====================
JWT_SECRET_KEY=replace-me-with-at-least-32-random-characters  # Signs tokens; min 32 chars
JWT_ALGORITHM=HS256                     # Signing algorithm
ACCESS_TOKEN_EXPIRE_MINUTES=15          # Access token lifetime
REFRESH_TOKEN_EXPIRE_DAYS=7             # Refresh token lifetime

# ==================== Lead Settings ====================
DEFAULT_COUNTRY_CODE=91                 # Added in front of 10-digit phone numbers

# ==================== Seed Settings ====================
ADMIN_EMAIL=admin@example.com           # First admin, created by the seed
ADMIN_PASSWORD=change-me-please         # Their password
ADMIN_FULL_NAME=Admin                   # Their display name
DEMO_PASSWORD=                          # Optional: also seed a demo manager + staff
cp .env.example .env
# make a real secret and paste it into .env as JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"
0.5 .gitignore
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
logs/
.DS_Store
0.6 Empty package folders
mkdir -p app/core/crud app/apis app/user/auth_management app/lead app/follow_up scripts tests logs
touch app/__init__.py app/core/__init__.py app/apis/__init__.py \
      app/user/__init__.py app/user/auth_management/__init__.py \
      app/lead/__init__.py app/follow_up/__init__.py tests/__init__.py
(app/core/crud/__init__.py gets real content in Step 2.)
Check: docker compose ps shows postgres running, and git status does not list .env.
Commit: create the public repo crm-leads-api on GitHub, then:
git add .
git commit -m "Set up project skeleton, pyproject and Postgres compose file"
git branch -M main
git remote add origin https://github.com/<your-username>/crm-leads-api.git
git push -u origin main
Step 1: Core foundation: settings, database, Base, time and logging
Goal: the reusable files in app/core/ that every module imports. None of them knows about leads or users.
1.1 app/core/settings.py
Why: secrets and URLs are never hard-coded. Herbally rules: UPPER_CASE fields grouped by section, required secrets have no default (the app refuses to start without them), secrets are validated, and the settings object is built once (@lru_cache).
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # ==================== App Settings ====================
    APP_NAME: str = "crm_leads_api"
    DEBUG: bool = False
    TESTING: bool = False
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = []
    LOG_DIR: str = "logs"

    # ==================== Database Settings ====================
    DATABASE_URL: str = Field(..., description="Async SQLAlchemy URL (postgresql+asyncpg://...)")

    # ==================== JWT Settings ====================
    JWT_SECRET_KEY: str = Field(..., description="Secret key for signing tokens")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ==================== Lead Settings ====================
    DEFAULT_COUNTRY_CODE: str = "91"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_secret_keys(cls, v: str, info) -> str:
        if not v or len(v) < 32:
            raise ValueError(f"{info.field_name} must be at least 32 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
• Field(...) means required: if DATABASE_URL is missing, startup fails with a clear error. That is what you want.
• Real environment variables win over .env. The tests use this to point at crm_test.
1.2 app/core/database.py
Why: the engine holds the connection pool (one per app). A session is one unit of work (one per request). get_session is a FastAPI dependency: it opens a session, hands it to the route, and closes it after the response. If nothing called commit(), closing rolls everything back.
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import settings

# Tests run each test in its own event loop, so they must not reuse pooled connections.
engine_options = {"poolclass": NullPool} if settings.TESTING else {"pool_pre_ping": True}
engine = create_async_engine(settings.DATABASE_URL, **engine_options)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
expire_on_commit=False: after commit(), SQLAlchemy normally "forgets" every attribute and reloads it on the next access. In async code that hidden reload crashes (MissingGreenlet). Turning it off keeps the values we just saved.
1.3 app/core/models.py
from enum import Enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every model inherits from this. Alembic reads Base.metadata."""


def enum_values(enum_cls: type[Enum]) -> list[str]:
    """Store an Enum's lowercase *values* in the database, not its UPPER member names."""
    return [member.value for member in enum_cls]
Why enum_values: our enums look like Role.ADMIN = "admin". By default SQLAlchemy would store the name ADMIN. Passing values_callable=enum_values makes the database store admin, the same text the API sends and receives.
1.4 app/core/utils.py
from datetime import datetime, timezone


def utc_now() -> datetime:
    """The one way to get 'now' in this project: timezone-aware, always UTC."""
    return datetime.now(timezone.utc)
Never use datetime.utcnow(): it is deprecated and returns a time with no timezone.
1.5 app/core/logging.py
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
MAX_BYTES = 5_000_000
BACKUP_COUNT = 5


def setup_logging(app_name: str, log_dir: str = "logs", debug: bool = False) -> None:
    Path(log_dir).mkdir(exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    all_logs = RotatingFileHandler(Path(log_dir) / f"{app_name}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    all_logs.setFormatter(formatter)

    error_logs = RotatingFileHandler(
        Path(log_dir) / f"{app_name}_errors.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    error_logs.setLevel(logging.ERROR)
    error_logs.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.handlers = [console, all_logs, error_logs]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
Usage everywhere else: logger = get_logger(__name__), then logger.info("Lead created", extra={"lead_id": str(lead.id)}).
Check:
python -c "from app.core.settings import settings; print(settings.DATABASE_URL)"
Then set JWT_SECRET_KEY=short in .env, run it again, and read the validation error. Put the long secret back.
Commit: Add core settings, database session, base model, utc_now and logging
Step 2: Core: exceptions, one error shape, list params and generic CRUD
Goal: the rest of the reusable plumbing. After this step app/core/ is complete except main.py, middleware.py and alembic_models_import.py.
2.1 app/core/exceptions.py
Two halves in one file. The top half is a small family of exception classes. Modules subclass them in their own exceptions.py (for example LeadNotFoundException(NotFoundException)). The bottom half is the global handlers that turn any error into the brief's shape: {"error": {"code", "message", "details"}}.
Why not HTTPException subclasses as Herbally's §9.1 shows: the brief says services must raise our own exceptions, not HTTPException. Our classes still carry status_code, so they read almost the same.
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


# ==================== Exception classes ====================

class AppException(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "Bad request"

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None):
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        super().__init__(self.message)


class UnauthorizedException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Not authenticated"


class ForbiddenException(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You don't have permission to do this"


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Not found"


class ConflictException(AppException):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Conflict"


class UnprocessableException(AppException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "The request has invalid fields"


# ==================== Response helpers ====================

def error_response(status_code: int, code: str, message: str, details: Any = None, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
        headers=headers,
    )


def _validation_details(errors) -> list[dict]:
    details = []
    for err in errors:
        loc = list(err.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path"):
            loc = loc[1:]
        details.append({
            "field": ".".join(str(part) for part in loc) or None,
            "message": err["msg"].removeprefix("Value error, "),
        })
    return details


# ==================== Handlers ====================

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "validation_error", "The request has invalid fields", _validation_details(exc.errors()))


async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return error_response(422, "validation_error", "The request has invalid fields", _validation_details(exc.errors()))


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("Integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
    return error_response(409, "conflict", "This conflicts with existing data")


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("Database unavailable on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(503, "service_unavailable", "The database is unavailable. Please try again.")


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong. Please try again later.")


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return error_response(400, "bad_request", str(exc))


async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return error_response(403, "forbidden", "You don't have permission to do this")


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), message,
                          headers=getattr(exc, "headers", None))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "Something went wrong. Please try again later.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(OperationalError, operational_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(PermissionError, permission_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
Things to understand:
• FastAPI picks the most specific handler. OperationalError is a kind of SQLAlchemyError, so a dead database gives 503, and other database errors give 500. Pydantic's ValidationError is a kind of ValueError, but its own handler wins.
• The IntegrityError handler is a safety net. Services still catch it themselves when they need a specific code like duplicate_phone.
• 500 and 503 responses never include the exception text, SQL or a stack trace. The full error goes to logs/<APP_NAME>_errors.log.
• Pydantic says loc = ("body", "phone") and "Value error, Phone must have...". We drop "body" and the prefix so the client sees {"field": "phone", "message": "Phone must have..."}.
2.2 app/core/schemas.py
Why: every list endpoint takes the same paging and sorting parameters, so they live in one base class. Each module subclasses it and adds its own filters.
from enum import Enum

from pydantic import BaseModel, Field


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class ListParams(BaseModel):
    """Base query parameters for every list endpoint."""

    sort_by: str = "created_at"
    sort_order: SortOrder = SortOrder.DESC
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)
Adapted from Herbally: the brief fixes the query names limit and offset, with a default of 20 and a maximum of 100, so we use those instead of skip and a 500 maximum.
2.3 app/core/crud/base.py
Why: most modules need the same three operations: get by id, create, update. CRUDBase writes them once. Each module's CRUD class inherits them and adds only what's different.
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """Generic database operations. NEVER commits: the service owns the transaction."""

    def __init__(self, model: type[ModelType]):
        self.model = model

    async def get(self, session: AsyncSession, id: uuid.UUID) -> ModelType | None:
        return await session.get(self.model, id)

    async def create(self, session: AsyncSession, *, obj_in: CreateSchemaType | dict[str, Any]) -> ModelType:
        data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump()
        db_obj = self.model(**data)
        session.add(db_obj)
        await session.flush()          # sends the INSERT, so DB errors appear here
        await session.refresh(db_obj)  # reloads it, including joined relationships
        return db_obj

    async def update(
        self, session: AsyncSession, *, db_obj: ModelType, obj_in: UpdateSchemaType | dict[str, Any]
    ) -> ModelType:
        data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(db_obj, field, value)
        await session.flush()
        await session.refresh(db_obj)
        return db_obj
flush() vs commit(): flush() sends the SQL to PostgreSQL inside the open transaction. A unique-constraint error appears right there. Nothing is permanent until the service calls commit(). If the request fails first, it all rolls back. This is how one service method can make several CRUD calls and still save all or nothing.
2.4 app/core/crud/helpers.py
from enum import Enum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import SortOrder


def apply_sorting(model, sort_by: str | Enum, sort_order: SortOrder) -> list:
    """ORDER BY clauses, always ending with id as a tiebreaker so pages never skip or repeat rows."""
    field = sort_by.value if isinstance(sort_by, Enum) else sort_by
    column = getattr(model, field)
    primary = column.asc() if sort_order == SortOrder.ASC else column.desc()
    return [primary, model.id.desc()]


async def paginated_select(
    session: AsyncSession, base_query: Select, *, skip: int, limit: int, order_clauses: list
) -> tuple[list[Any], int]:
    """One round-trip: the page of rows AND the total, via COUNT(*) OVER ()."""
    stmt = (
        base_query.add_columns(func.count().over().label("total_count"))
        .order_by(*order_clauses)
        .offset(skip)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    if rows:
        return [row[0] for row in rows], rows[0].total_count
    if skip == 0:
        return [], 0
    # Asked for a page past the end: no row to read the total from, so count separately.
    count_stmt = select(func.count()).select_from(base_query.order_by(None).subquery())
    return [], (await session.scalar(count_stmt)) or 0
COUNT(*) OVER () is a window function: PostgreSQL adds the total number of matching rows to every row it returns, before LIMIT cuts the page. So the total and the page come from the same WHERE, in one query.
2.5 app/core/crud/__init__.py
from .base import CRUDBase
from .helpers import apply_sorting, paginated_select

__all__ = ["CRUDBase", "apply_sorting", "paginated_select"]
Check:
python -c "from app.core.crud import CRUDBase, paginated_select; from app.core.exceptions import register_exception_handlers; print('ok')"
Commit: Add core exceptions, global handlers, list params and generic CRUD helpers
Step 3: User model, Alembic and the first migration
Goal: a users table in PostgreSQL, created only by an Alembic migration (never create_all), in a timestamped migration file.
3.1 app/user/enums.py
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"
(str, Enum) makes each member a real string: Role.ADMIN == "admin" is True, and FastAPI shows a dropdown in Swagger. One trap: f"{Role.ADMIN}" prints Role.ADMIN in Python 3.11+, so in messages always write Role.ADMIN.value.
3.2 app/user/models.py
Herbally model rules: UUID primary key, DateTime(timezone=True) with utc_now, nullable= always written out, and indexes named explicitly in __table_args__.
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, String, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, enum_values
from app.core.utils import utc_now
from app.user.enums import Role


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_role", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, values_callable=enum_values), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
Things to notice:
• The UniqueConstraint on email is a database rule. It is what stops two identical requests at the same moment from creating two users. We name it uq_users_email so the service can recognise it.
• Enum(Role, ...) keeps SQLAlchemy's default type name, which is the class name lowercased: the PostgreSQL type is called role.
• updated_at isn't in the brief's table for users, but Herbally adds it to every model. Extra columns are allowed.
• default= runs in Python on insert. server_default= is written into the table itself.
3.3 app/core/alembic_models_import.py
Why: Alembic only sees tables whose model classes were imported. This one file is the single source of truth. You add a line here for each new module (Steps 6 and 9), never in env.py.
"""Every model, imported in one place so Alembic autogenerate can see it."""
from app.core.models import Base  # noqa: F401

# User module
from app.user.models import User  # noqa: F401

# Add every new module here ↓
3.4 Set up Alembic (async template)
alembic init -t async migrations
In alembic.ini, find the commented file_template line, uncomment it and set it to Herbally's format. Also check prepend_sys_path = . is present so app can be imported:
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
prepend_sys_path = .
In migrations/env.py, change the part below the existing imports to:
from app.core.alembic_models_import import *  # noqa: F401,F403  (registers every table)
from app.core.models import Base
from app.core.settings import settings

config = context.config
# '%' must be doubled because alembic's config format treats it specially
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
Leave the rest of env.py (run_migrations_offline, do_run_migrations, run_async_migrations) as generated.
3.5 Generate and review the first migration
alembic revision --autogenerate -m "create users table"
The new file is named like migrations/versions/2026_10_05_1030-3f2a9c1e5b7d_create_users_table.py. Always open and read it. Autogenerate is a helper, not a source of truth. Keep the revision / down_revision lines it made, and make the body look like this:
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers ... (keep the generated ones)

role_enum = postgresql.ENUM("admin", "manager", "staff", name="role", create_type=False)


def upgrade() -> None:
    role_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_role", "users", ["role"])


def downgrade() -> None:
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
    role_enum.drop(op.get_bind(), checkfirst=True)
Why we create the enum type by hand: PostgreSQL stores an enum as its own type. Autogenerate creates it, but its downgrade() forgets to drop it, and when two columns share one enum (Step 6) it tries to create it twice and crashes. create_type=False plus an explicit .create() / .drop() keeps upgrade and downgrade clean. Every migration in this guide uses the same pattern.
3.6 Run it
alembic upgrade head
docker compose exec postgres psql -U crm -d crm -c "\d users"
You should see the columns, uq_users_email and ix_users_role. Test the way back too, then go forward again:
alembic downgrade base
alembic upgrade head
Day-1 check-in material: be ready to explain why email is unique in the database (not just checked in code), why timestamps are timestamptz, why role is an enum, and why the enum stores admin and not ADMIN.
Commit: Add user model and first migration
Step 4: Auth: who is calling, and are they allowed?
Goal: POST /auth/login, POST /auth/refresh, GET /auth/me, the reusable get_current_active_user and require_roles(...) dependencies, and a running app. Everything about auth lives in app/user/, because it knows about users and roles, so it can't go in core/.
4.1 app/user/exceptions.py
from app.core.exceptions import ConflictException, NotFoundException, UnauthorizedException


class InvalidCredentialsException(UnauthorizedException):
    code = "invalid_credentials"
    message = "Invalid email or password"


class InvalidTokenException(UnauthorizedException):
    code = "invalid_token"
    message = "Invalid or expired token"


class InactiveUserException(UnauthorizedException):
    code = "inactive_user"
    message = "User not found or inactive"


class UserNotFoundException(NotFoundException):
    code = "user_not_found"
    message = "User not found"


class DuplicateEmailException(ConflictException):
    code = "duplicate_email"
    message = "A user with this email already exists"


class CannotDeactivateSelfException(ConflictException):
    code = "cannot_deactivate_self"
    message = "You can't deactivate your own account"
Each class only sets a code and a message. The status code comes from its parent in core.
4.2 app/user/schemas.py
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.user.enums import Role

FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# ==================== Requests ====================

class UserCreate(BaseModel):
    full_name: FullName
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value: str) -> str:
        return value.lower()


class UserActiveUpdate(BaseModel):
    is_active: bool


# ==================== Responses ====================

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserBriefResponse(BaseModel):
    """The small {id, full_name} object used inside leads and follow-ups."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
• Response schemas list what may leave the API. password_hash is not listed, so it can never leak.
• from_attributes=True lets Pydantic read user.full_name from a model object.
• Lowercasing the email means Asha@X.com and asha@x.com are stored the same way, so the unique constraint catches the duplicate.
4.3 app/user/crud.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDBase
from app.user.enums import Role
from app.user.models import User
from app.user.schemas import UserActiveUpdate, UserCreate


class UserCRUD(CRUDBase[User, UserCreate, UserActiveUpdate]):
    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        return await session.scalar(select(User).where(User.email == email.lower()))

    async def get_list(
        self, session: AsyncSession, *, role: Role | None = None, is_active: bool | None = None
    ) -> list[User]:
        stmt = select(User).order_by(User.created_at, User.id)
        if role is not None:
            stmt = stmt.where(User.role == role)
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
        return list(await session.scalars(stmt))


user_crud = UserCRUD(User)
user_crud is a module-level singleton (Herbally naming: <model>_crud). It inherits get, create and update from CRUDBase.
4.4 app/user/auth_management/security.py
Passwords: we store a hash, never the password. Argon2 is deliberately slow, so it blocks the CPU. Inside async def that would freeze every other request, so we run it in a worker thread with asyncio.to_thread.
Tokens: a JWT is a signed JSON payload. We put only the user id (sub), the token type and the expiry inside. We don't put the role in the token: the user is loaded from the database on every request, so a role change or a deactivation applies at once.
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.settings import settings
from app.user.exceptions import InvalidTokenException

_password_hash = PasswordHash.recommended()  # Argon2

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_password_hash.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_password_hash.verify, password, password_hash)


# Checked when the email doesn't exist, so a failed login takes the same time either way.
DUMMY_PASSWORD_HASH = _password_hash.hash("dummy-password-for-timing")


def _create_token(user_id: uuid.UUID, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "type": token_type, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: uuid.UUID) -> str:
    return _create_token(user_id, ACCESS_TOKEN_TYPE, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(user_id: uuid.UUID) -> str:
    return _create_token(user_id, REFRESH_TOKEN_TYPE, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str, expected_type: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise InvalidTokenException("Token has expired")
    except jwt.InvalidTokenError:
        raise InvalidTokenException()

    if payload.get("type") != expected_type:
        raise InvalidTokenException("Invalid token type")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise InvalidTokenException()
The type check is what makes "a refresh token sent as an access token gets 401" work. Both tokens use the same secret, so without it a 7-day refresh token would work as an access token.
4.5 app/user/permissions.py
Why: Herbally keeps "who can do what" in each module's permissions.py. The brief gives us three fixed roles instead of a permission table, so here the constants are role sets, matching the brief's section 3 table.
from app.user.enums import Role

CAN_CREATE_USERS = (Role.ADMIN,)
CAN_LIST_USERS = (Role.ADMIN, Role.MANAGER)
CAN_MANAGE_USERS = (Role.ADMIN,)  # activate / deactivate
4.6 app/user/dependencies.py
from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.user.auth_management.security import ACCESS_TOKEN_TYPE, decode_token
from app.user.crud import user_crud
from app.user.enums import Role
from app.user.exceptions import InactiveUserException
from app.user.models import User

# auto_error=False: we raise our own 401 instead of FastAPI's default
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_active_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise UnauthorizedException()
    user_id = decode_token(credentials.credentials, ACCESS_TOKEN_TYPE)
    user = await user_crud.get(session, user_id)
    if user is None or not user.is_active:
        raise InactiveUserException()
    return user


def require_roles(*roles: Role) -> Callable[..., Awaitable[None]]:
    """Role guard. Use as:  _: None = Depends(require_roles(*CAN_ASSIGN_LEADS))"""

    async def guard(current_user: User = Depends(get_current_active_user)) -> None:
        if current_user.role not in roles:
            raise ForbiddenException()

    return guard
get_current_active_user carries three rules from the brief:
1. No token → 401 (credentials is None).
2. Refresh token used as access token → 401 (decode_token(..., ACCESS_TOKEN_TYPE)).
3. A deactivated user's existing token stops working on the next request: the user is loaded from the database every time and is_active is checked.
require_roles returns a dependency (a closure that remembers roles). It follows Herbally's guard shape _: None = Depends(...). FastAPI runs get_current_active_user and get_session once per request even when several dependencies ask for them, so the route, the guard and the service share the same session.
4.7 app/user/auth_management/schemas.py
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
4.8 app/user/auth_management/service.py
from sqlalchemy.ext.asyncio import AsyncSession

from app.user.auth_management.schemas import AccessTokenResponse, TokenResponse
from app.user.auth_management.security import (
    DUMMY_PASSWORD_HASH,
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.user.crud import user_crud
from app.user.exceptions import InactiveUserException, InvalidCredentialsException


class AuthService:
    async def login(self, session: AsyncSession, email: str, password: str) -> TokenResponse:
        user = await user_crud.get_by_email(session, email)
        if user is None:
            await verify_password(password, DUMMY_PASSWORD_HASH)  # same timing as a real check
            raise InvalidCredentialsException()
        if not await verify_password(password, user.password_hash) or not user.is_active:
            raise InvalidCredentialsException()
        return TokenResponse(
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
        )

    async def refresh(self, session: AsyncSession, refresh_token: str) -> AccessTokenResponse:
        user_id = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
        user = await user_crud.get(session, user_id)
        if user is None or not user.is_active:
            raise InactiveUserException()
        return AccessTokenResponse(access_token=create_access_token(user.id))


auth_service = AuthService()
One message for every login failure (unknown email, wrong password, deactivated). Different messages would tell an attacker which emails exist.
4.9 app/user/auth_management/routes.py
Notice how thin the routes are: inject, call the service, return.
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.user.auth_management.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)
from app.user.auth_management.service import auth_service
from app.user.dependencies import get_current_active_user
from app.user.models import User
from app.user.schemas import UserResponse

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


@auth_router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await auth_service.login(session, data.email, data.password)


@auth_router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(data: RefreshRequest, session: AsyncSession = Depends(get_session)):
    return await auth_service.refresh(session, data.refresh_token)


@auth_router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_active_user)):
    return current_user
4.10 app/core/middleware.py
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.settings import settings


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{(time.perf_counter() - start) * 1000:.1f}ms"
        return response
4.11 app/apis/v1.py (the router registry)
from fastapi import APIRouter

from app.user.auth_management.routes import auth_router

router = APIRouter()

router.include_router(auth_router)
# add new module routers here ↓
4.12 app/core/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.apis.v1 import router as api_v1_router
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import register_middleware
from app.core.settings import settings

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("%s starting", settings.APP_NAME)
    yield
    await engine.dispose()
    logger.info("%s stopped", settings.APP_NAME)


def create_app() -> FastAPI:
    setup_logging(settings.APP_NAME, settings.LOG_DIR, settings.DEBUG)

    app = FastAPI(title="CRM Leads API", version="1.0.0", lifespan=lifespan)
    register_middleware(app)
    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
create_app() is an app factory: everything about building the app lives in one function. lifespan runs code at startup and shutdown; here it closes the connection pool cleanly.
Check:
uvicorn app.core.main:app --reload
Open http://127.0.0.1:8000/docs. Call GET /api/v1/auth/me without a token: you get 401 in our error shape. Look in logs/ for the two log files. Logging in works after Step 5 creates the first user.
Commits: Add user schemas, CRUD and auth exceptions, then Add JWT login, refresh, me and role guard, then Add app factory, middleware and v1 router registry
Step 5: User service, routes, public API and the seed
Goal: an admin can create, list and deactivate users, the user module exposes a clean public API, and the seed creates the first admin so you can log in.
5.1 app/user/service.py
The service owns the transaction: it calls the CRUD (which only flushes), then commit().
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.user.auth_management.security import hash_password
from app.user.crud import user_crud
from app.user.enums import Role
from app.user.exceptions import (
    CannotDeactivateSelfException,
    DuplicateEmailException,
    UserNotFoundException,
)
from app.user.models import User
from app.user.schemas import UserCreate


class UserService:
    async def create_user(self, session: AsyncSession, data: UserCreate) -> User:
        password_hash = await hash_password(data.password)
        try:
            user = await user_crud.create(session, obj_in={
                "full_name": data.full_name,
                "email": data.email,
                "password_hash": password_hash,
                "role": data.role,
            })
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if "uq_users_email" in str(exc.orig):
                raise DuplicateEmailException() from exc
            raise
        return user

    async def list_users(
        self, session: AsyncSession, role: Role | None, is_active: bool | None
    ) -> list[User]:
        return await user_crud.get_list(session, role=role, is_active=is_active)

    async def set_active(
        self, session: AsyncSession, user_id: uuid.UUID, is_active: bool, current_user: User
    ) -> User:
        user = await user_crud.get(session, user_id)
        if user is None:
            raise UserNotFoundException()
        if user.id == current_user.id and not is_active:
            raise CannotDeactivateSelfException()
        user = await user_crud.update(session, db_obj=user, obj_in={"is_active": is_active})
        await session.commit()
        return user


user_service = UserService()
The try / except IntegrityError pattern is the most important idea in this step. We do not first check "does this email exist?" and then insert: two requests at the same moment could both pass that check. Instead we insert, and the database's unique constraint refuses the second one during flush(). We recognise our constraint by name and return 409 duplicate_email. Any other integrity error is re-raised, and the global handler turns it into a generic 409. You'll use the same pattern for duplicate phones.
The service builds the dict for user_crud.create itself, because the model has password_hash while the request has password.
5.2 app/user/routes.py
Herbally dependency style: = Depends(...) defaults, the role guard as _: None.
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.user.dependencies import get_current_active_user, require_roles
from app.user.enums import Role
from app.user.models import User
from app.user.permissions import CAN_CREATE_USERS, CAN_LIST_USERS, CAN_MANAGE_USERS
from app.user.schemas import UserActiveUpdate, UserCreate, UserResponse
from app.user.service import user_service

user_router = APIRouter(prefix="/users", tags=["Users"])


@user_router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    _: None = Depends(require_roles(*CAN_CREATE_USERS)),
    session: AsyncSession = Depends(get_session),
):
    return await user_service.create_user(session, data)


@user_router.get("", response_model=list[UserResponse])
async def list_users(
    role: Role | None = None,
    is_active: bool | None = None,
    _: None = Depends(require_roles(*CAN_LIST_USERS)),
    session: AsyncSession = Depends(get_session),
):
    return await user_service.list_users(session, role, is_active)


@user_router.patch("/{user_id}/active", response_model=UserResponse)
async def set_user_active(
    user_id: uuid.UUID,
    data: UserActiveUpdate,
    _: None = Depends(require_roles(*CAN_MANAGE_USERS)),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await user_service.set_active(session, user_id, data.is_active, current_user)
Decision for the README: the brief asks you to test deactivation but lists no endpoint for it. We added PATCH /users/{user_id}/active (admin only), and an admin can't deactivate themselves.
Why GET /users doesn't use ListParams: the brief returns a plain list with no total and no paging. Herbally's list standard is for endpoints that expose filters, sorting and a total.
5.3 app/user/__init__.py (the module's public API)
from .models import User
from .auth_management.routes import auth_router
from .routes import user_router

__all__ = ["User", "auth_router", "user_router"]
Import rule that keeps you out of circular-import trouble: only code outside the module (app/apis/v1.py) imports from the package (from app.user import user_router). Code inside the project always imports from the submodule (from app.user.models import User).
5.4 Update app/apis/v1.py
from fastapi import APIRouter

from app.user import auth_router, user_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(user_router)
# add new module routers here ↓
5.5 app/user/seed.py
Herbally seed rules: idempotent (safe to run many times; it only inserts what's missing), runnable with python -m app.user.seed, and dispose_engine=False when called from inside a running app.
"""Idempotent seeder: the first admin, plus optional demo users.

Run:  python -m app.user.seed
"""
import asyncio

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal, engine
from app.core.logging import get_logger, setup_logging
from app.core.settings import settings
from app.user.auth_management.security import hash_password
from app.user.crud import user_crud
from app.user.enums import Role

logger = get_logger(__name__)


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    ADMIN_EMAIL: str
    ADMIN_PASSWORD: str
    ADMIN_FULL_NAME: str = "Admin"
    DEMO_PASSWORD: str | None = None


async def ensure_user(session: AsyncSession, email: str, password: str, full_name: str, role: Role) -> None:
    if await user_crud.get_by_email(session, email):
        logger.info("Seed: %s already exists, skipped", email)
        return
    await user_crud.create(session, obj_in={
        "full_name": full_name,
        "email": email.lower(),
        "password_hash": await hash_password(password),
        "role": role,
    })
    logger.info("Seed: created %s (%s)", email, role.value)


async def seed(dispose_engine: bool = True) -> None:
    seed_settings = SeedSettings()
    async with SessionLocal() as session:
        await ensure_user(
            session, seed_settings.ADMIN_EMAIL, seed_settings.ADMIN_PASSWORD,
            seed_settings.ADMIN_FULL_NAME, Role.ADMIN,
        )
        if seed_settings.DEMO_PASSWORD:
            await ensure_user(session, "manager@example.com", seed_settings.DEMO_PASSWORD, "Demo Manager", Role.MANAGER)
            await ensure_user(session, "staff@example.com", seed_settings.DEMO_PASSWORD, "Demo Staff", Role.STAFF)
        await session.commit()
    if dispose_engine:
        await engine.dispose()


def main() -> None:
    setup_logging(settings.APP_NAME, settings.LOG_DIR, settings.DEBUG)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
5.6 scripts/seed.py (the path the brief asks for)
"""The brief requires scripts/seed.py. The real seeder lives in app/user/seed.py.

Run from the project root:  python -m scripts.seed
"""
from app.user.seed import main

if __name__ == "__main__":
    main()
Decision for the README: Herbally runs its seed on startup. We don't, because the brief says the seed script creates the admin. Run it once after migrating.
Check (end of day 1):
python -m scripts.seed
python -m scripts.seed          # second run: "already exists, skipped"
uvicorn app.core.main:app --reload
In /docs:
1. POST /api/v1/auth/login with your ADMIN_EMAIL / ADMIN_PASSWORD. Copy the access_token.
2. Click Authorize (top right) and paste the token.
3. GET /api/v1/auth/me returns the admin, with no password_hash.
4. POST /api/v1/users to create a staff user. Send the same email again in CAPITALS: 409 duplicate_email.
5. Log in as the staff user, authorize with their token, try GET /api/v1/users: 403.
Commits: Add create, list and deactivate user endpoints, then Add idempotent user seed and scripts/seed.py entry point
Step 6: Lead enums, models, phone normalisation and the leads migration
Goal: the leads and lead_status_history tables, and a phone function that turns every way of writing a number into one stored form.
6.1 app/lead/enums.py
from enum import Enum


class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL_SENT = "proposal_sent"
    WON = "won"
    LOST = "lost"


class LeadSource(str, Enum):
    WEBSITE = "website"
    REFERRAL = "referral"
    WALK_IN = "walk_in"
    PHONE_CALL = "phone_call"
    SOCIAL_MEDIA = "social_media"
    OTHER = "other"


class LeadSortField(str, Enum):
    """Columns GET /leads may sort by. Anything else is a 422, and Swagger shows a dropdown."""
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    NAME = "name"
    STATUS = "status"
6.2 app/lead/models.py
Naming idea used from here on: the database column is assigned_to (as the brief says), but in Python the UUID column is assigned_to_id and the related User object is assigned_to. Rule of thumb: _id = a UUID, no suffix = the object. This lets LeadResponse read lead.assigned_to.full_name directly.
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, enum_values
from app.core.utils import utc_now
from app.lead.enums import LeadSource, LeadStatus

if TYPE_CHECKING:
    from app.user.models import User

# One PostgreSQL type (default name "leadstatus"), used by three columns.
lead_status_enum = Enum(LeadStatus, values_callable=enum_values)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("phone", name="uq_leads_phone"),
        Index("ix_leads_status", "status"),
        Index("ix_leads_assigned_to", "assigned_to"),
        Index("ix_leads_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[LeadSource] = mapped_column(Enum(LeadSource, values_callable=enum_values), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[LeadStatus] = mapped_column(lead_status_enum, default=LeadStatus.NEW, nullable=False)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        "assigned_to", UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        "created_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # lazy="joined": load the two users in the same SELECT (async code can't lazy-load later)
    assigned_to: Mapped[Optional["User"]] = relationship(foreign_keys=[assigned_to_id], lazy="joined")
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id], lazy="joined")


class LeadStatusHistory(Base):
    __tablename__ = "lead_status_history"
    __table_args__ = (
        CheckConstraint(
            "to_status <> 'lost' OR reason IS NOT NULL",
            name="ck_lead_status_history_lost_needs_reason",
        ),
        Index("ix_lead_status_history_lead_id", "lead_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    from_status: Mapped[LeadStatus] = mapped_column(lead_status_enum, nullable=False)
    to_status: Mapped[LeadStatus] = mapped_column(lead_status_enum, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by_id: Mapped[uuid.UUID] = mapped_column(
        "changed_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    changed_by: Mapped["User"] = relationship(foreign_keys=[changed_by_id], lazy="joined")
Things to notice:
• Two foreign keys point at users, so each relationship says which column it uses (foreign_keys=[...]).
• Index names follow ix_<table>_<column>. The strings in Index(...) are database column names ("assigned_to"), not Python attribute names.
• The CheckConstraint is a second line of defence: even if a bug skipped the Python check, the database refuses a "lost" row with no reason.
• lead_status_history keeps the brief's table name, although Herbally prefers plural names. It is append-only, so it has changed_at and no updated_at.
Add to app/core/alembic_models_import.py:
# Lead module
from app.lead.models import Lead, LeadStatusHistory  # noqa: F401
6.3 app/lead/phone.py
Phone normalisation is a lead rule, so it lives in the lead module, not in core/utils.py.
Decision (write it in the README): a 10-digit number with no + is treated as an Indian number and gets +91 in front (from DEFAULT_COUNTRY_CODE). So 98470 12345, 9847012345 and +91 98470 12345 are all stored as +919847012345 and count as the same lead.
Input
Stored
98470 12345
+919847012345
9847012345
+919847012345
+91 98470-12345
+919847012345
919847012345 (11–15 digits, no +)
+919847012345
12345
rejected, 422
++919847012345
rejected, 422
import re

_SEPARATORS = re.compile(r"[\s-]")
_VALID = re.compile(r"\+?\d{10,15}")


def normalize_phone(raw: str, default_country_code: str) -> str:
    cleaned = _SEPARATORS.sub("", raw)
    if not _VALID.fullmatch(cleaned):
        raise ValueError("Phone must have 10 to 15 digits, with an optional leading +")
    if cleaned.startswith("+"):
        return cleaned
    if len(cleaned) == 10:
        return f"+{default_country_code}{cleaned}"
    return f"+{cleaned}"
It is a plain function with no database and no FastAPI, so it's trivial to unit-test. Raising ValueError is how Pydantic validators report a bad value (used in Step 7).
6.4 The migration
alembic revision --autogenerate -m "create leads and lead status history"
Keep the generated revision lines and make the body match this. leadstatus is used by three columns in two tables: exactly the case where autogenerate would try to create the type twice. Foreign keys and primary keys are left unnamed (SQLAlchemy default, as Herbally says); unique, check and index names are explicit.
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

lead_status_enum = postgresql.ENUM(
    "new", "contacted", "qualified", "proposal_sent", "won", "lost",
    name="leadstatus", create_type=False,
)
lead_source_enum = postgresql.ENUM(
    "website", "referral", "walk_in", "phone_call", "social_media", "other",
    name="leadsource", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    lead_status_enum.create(bind, checkfirst=True)
    lead_source_enum.create(bind, checkfirst=True)

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("source", lead_source_enum, nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("status", lead_status_enum, nullable=False),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone", name="uq_leads_phone"),
    )
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_assigned_to", "leads", ["assigned_to"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    op.create_table(
        "lead_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", lead_status_enum, nullable=False),
        sa.Column("to_status", lead_status_enum, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "to_status <> 'lost' OR reason IS NOT NULL",
            name="ck_lead_status_history_lost_needs_reason",
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_status_history_lead_id", "lead_status_history", ["lead_id"])


def downgrade() -> None:
    op.drop_table("lead_status_history")  # drops its indexes too
    op.drop_table("leads")
    bind = op.get_bind()
    lead_source_enum.drop(bind, checkfirst=True)
    lead_status_enum.drop(bind, checkfirst=True)
Check:
alembic upgrade head
docker compose exec postgres psql -U crm -d crm -c "\d leads"
alembic downgrade -1 && alembic upgrade head
python -c "from app.lead.phone import normalize_phone as n; print(n('98470 12345','91'), n('+91 98470-12345','91'))"
The last line should print +919847012345 +919847012345. ls migrations/versions shows two timestamped files in order.
Commits: Add lead models and migration, then Add phone normalisation
Step 7: Leads: create, list, view and assign
Goal: four endpoints. The rule "staff only see their own leads, and get 404 for anyone else's" is decided once, in the service, and enforced in SQL by the CRUD.
7.1 app/lead/exceptions.py
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnprocessableException,
)
from app.lead.enums import LeadStatus


class LeadNotFoundException(NotFoundException):
    code = "lead_not_found"
    message = "Lead not found"


class DuplicatePhoneException(ConflictException):
    code = "duplicate_phone"
    message = "A lead with this phone number already exists"


class InvalidAssigneeException(UnprocessableException):
    code = "invalid_assignee"
    message = "Assignee must be an active staff member or manager"


class AssigneeNotAllowedException(ForbiddenException):
    code = "assignee_not_allowed"
    message = "Staff can't choose an assignee; your leads are assigned to you"


class InvalidStatusTransitionException(ConflictException):
    code = "invalid_status_transition"

    def __init__(self, current: LeadStatus, target: LeadStatus, allowed: tuple[LeadStatus, ...]):
        allowed_text = ", ".join(s.value for s in allowed) if allowed else "none, this status is final"
        super().__init__(f"Can't move a lead from '{current.value}' to '{target.value}'. Allowed: {allowed_text}.")


class ReopenNotAllowedException(ForbiddenException):
    code = "reopen_not_allowed"
    message = "Only an admin or manager can reopen a lost lead"


class LostReasonRequiredException(UnprocessableException):
    message = "A reason is required when marking a lead as lost"

    def __init__(self):
        super().__init__(details=[{"field": "reason", "message": "Required when status is 'lost'"}])
The last three are used in Step 8. Note .value in the message: with (str, Enum), f"{current}" would print LeadStatus.NEW.
7.2 app/lead/permissions.py
Herbally pairs every scoped resource with a read_all override. Here that override is a role set: roles in CAN_READ_ALL_LEADS see every lead, and everyone else sees only leads assigned to them.
from app.user.enums import Role

CAN_READ_ALL_LEADS = (Role.ADMIN, Role.MANAGER)     # the "leads:read_all" override
CAN_ASSIGN_LEADS = (Role.ADMIN, Role.MANAGER)
CAN_REOPEN_LOST_LEADS = (Role.ADMIN, Role.MANAGER)
ASSIGNABLE_ROLES = (Role.STAFF, Role.MANAGER)        # who a lead may be assigned to
7.3 app/lead/schemas.py
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.core.schemas import ListParams, SortOrder
from app.core.settings import settings
from app.lead.enums import LeadSortField, LeadSource, LeadStatus
from app.lead.phone import normalize_phone
from app.user.schemas import UserBriefResponse

LeadName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# ==================== Requests ====================

class LeadCreate(BaseModel):
    name: LeadName
    phone: str
    email: EmailStr | None = None
    source: LeadSource
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    assigned_to: uuid.UUID | None = None

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str) -> str:
        return normalize_phone(value, settings.DEFAULT_COUNTRY_CODE)


class LeadAssign(BaseModel):
    assigned_to: uuid.UUID


class LeadListParams(ListParams):
    sort_by: LeadSortField = LeadSortField.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC

    # Filters
    status: LeadStatus | None = None
    assigned_to: uuid.UUID | None = None
    source: LeadSource | None = None
    search: str | None = Field(None, max_length=100)


# ==================== Responses ====================

class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    source: LeadSource
    notes: str | None
    status: LeadStatus
    assigned_to: UserBriefResponse | None
    created_by: UserBriefResponse
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    limit: int
    offset: int
• LeadListParams inherits limit (1–100, default 20) and offset from ListParams, and overrides sort_by with the typed enum. limit=500 is rejected with 422 (decision: reject rather than silently cap).
• LeadResponse.assigned_to is filled from the Lead.assigned_to relationship, giving {"id": ..., "full_name": ...} exactly like the brief's example.
7.4 app/lead/crud.py
The CRUD builds SQL from the arguments it is given. It never decides who may see what: it just filters by owner_user_ids when the service passes them.
import re
import uuid

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDBase, apply_sorting, paginated_select
from app.core.schemas import SortOrder
from app.lead.enums import LeadSortField, LeadSource, LeadStatus
from app.lead.models import Lead, LeadStatusHistory
from app.lead.schemas import LeadAssign, LeadCreate


class LeadCRUD(CRUDBase[Lead, LeadCreate, LeadAssign]):
    async def get_scoped(
        self,
        session: AsyncSession,
        lead_id: uuid.UUID,
        *,
        owner_user_ids: list[uuid.UUID] | None,
        for_update: bool = False,
    ) -> Lead | None:
        stmt = select(Lead).where(Lead.id == lead_id)
        if owner_user_ids is not None:
            stmt = stmt.where(Lead.assigned_to_id.in_(owner_user_ids))
        if for_update:
            # Lock this lead's row until commit, and reload fresh values.
            stmt = stmt.with_for_update(of=Lead).execution_options(populate_existing=True)
        return await session.scalar(stmt)

    async def get_list_filtered(
        self,
        session: AsyncSession,
        *,
        skip: int,
        limit: int,
        sort_by: LeadSortField,
        sort_order: SortOrder,
        owner_user_ids: list[uuid.UUID] | None,
        status: LeadStatus | None = None,
        assigned_to: uuid.UUID | None = None,
        source: LeadSource | None = None,
        search: str | None = None,
    ) -> tuple[list[Lead], int]:
        query = select(Lead)
        if owner_user_ids is not None:
            query = query.where(Lead.assigned_to_id.in_(owner_user_ids))
        if assigned_to is not None:
            query = query.where(Lead.assigned_to_id == assigned_to)
        if status is not None:
            query = query.where(Lead.status == status)
        if source is not None:
            query = query.where(Lead.source == source)
        if search:
            term = search.strip()
            phone_term = re.sub(r"[\s-]", "", term)
            query = query.where(or_(Lead.name.ilike(f"%{term}%"), Lead.phone.ilike(f"%{phone_term}%")))

        return await paginated_select(
            session, query,
            skip=skip, limit=limit,
            order_clauses=apply_sorting(Lead, sort_by, sort_order),
        )


class LeadStatusHistoryCRUD(CRUDBase[LeadStatusHistory, BaseModel, BaseModel]):
    async def list_for_lead(self, session: AsyncSession, lead_id: uuid.UUID) -> list[LeadStatusHistory]:
        stmt = (
            select(LeadStatusHistory)
            .where(LeadStatusHistory.lead_id == lead_id)
            .order_by(LeadStatusHistory.changed_at, LeadStatusHistory.id)
        )
        return list(await session.scalars(stmt))


lead_crud = LeadCRUD(Lead)
lead_status_history_crud = LeadStatusHistoryCRUD(LeadStatusHistory)
7.5 app/lead/service.py
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.lead.crud import lead_crud
from app.lead.enums import LeadStatus
from app.lead.exceptions import (
    AssigneeNotAllowedException,
    DuplicatePhoneException,
    InvalidAssigneeException,
    LeadNotFoundException,
)
from app.lead.models import Lead
from app.lead.permissions import ASSIGNABLE_ROLES, CAN_ASSIGN_LEADS, CAN_READ_ALL_LEADS
from app.lead.schemas import LeadCreate, LeadListParams
from app.user.crud import user_crud
from app.user.models import User


class LeadService:
    @staticmethod
    def owner_scope(user: User) -> list[uuid.UUID] | None:
        """None = may see every lead. Otherwise only leads assigned to these user ids."""
        return None if user.role in CAN_READ_ALL_LEADS else [user.id]

    async def get_lead(
        self, session: AsyncSession, lead_id: uuid.UUID, user: User, *, for_update: bool = False
    ) -> Lead:
        lead = await lead_crud.get_scoped(
            session, lead_id, owner_user_ids=self.owner_scope(user), for_update=for_update
        )
        if lead is None:
            raise LeadNotFoundException()  # also when it exists but isn't theirs: never confirm it
        return lead

    async def _ensure_assignable(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        assignee = await user_crud.get(session, user_id)
        if assignee is None or not assignee.is_active or assignee.role not in ASSIGNABLE_ROLES:
            raise InvalidAssigneeException()

    async def create_lead(self, session: AsyncSession, data: LeadCreate, user: User) -> Lead:
        if user.role in CAN_ASSIGN_LEADS:
            assignee_id = data.assigned_to
            if assignee_id is not None:
                await self._ensure_assignable(session, assignee_id)
        else:
            if "assigned_to" in data.model_fields_set:
                raise AssigneeNotAllowedException()
            assignee_id = user.id

        try:
            lead = await lead_crud.create(session, obj_in={
                "name": data.name,
                "phone": data.phone,
                "email": data.email,
                "source": data.source,
                "notes": data.notes,
                "status": LeadStatus.NEW,
                "assigned_to_id": assignee_id,
                "created_by_id": user.id,
            })
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if "uq_leads_phone" in str(exc.orig):
                raise DuplicatePhoneException() from exc
            raise
        return lead

    async def list_leads(
        self, session: AsyncSession, params: LeadListParams, user: User
    ) -> tuple[list[Lead], int]:
        return await lead_crud.get_list_filtered(
            session,
            skip=params.offset,
            limit=params.limit,
            sort_by=params.sort_by,
            sort_order=params.sort_order,
            owner_user_ids=self.owner_scope(user),  # staff: own leads, whatever filters they send
            status=params.status,
            assigned_to=params.assigned_to,
            source=params.source,
            search=params.search,
        )

    async def assign_lead(
        self, session: AsyncSession, lead_id: uuid.UUID, assignee_id: uuid.UUID, user: User
    ) -> Lead:
        lead = await self.get_lead(session, lead_id, user)
        await self._ensure_assignable(session, assignee_id)
        lead = await lead_crud.update(session, db_obj=lead, obj_in={"assigned_to_id": assignee_id})
        await session.commit()  # status is not touched
        return lead


lead_service = LeadService()
Study these five ideas; they are the heart of the access-control marks:
1. 404, not 403, for other people's leads. Staff's query gets WHERE assigned_to IN (me). Someone else's lead isn't found, so we raise LeadNotFoundException. The API never confirms the lead exists.
2. The scope is decided in the service (owner_scope), as the brief requires, and applied in the CRUD as Herbally's owner_user_ids. The route never sees it.
3. Filters can't widen the scope. The owner condition is always added, and the user's filters only narrow it further.
4. Duplicate phone = database constraint + IntegrityError at flush(). Two simultaneous requests: one insert wins, the other gets 409 duplicate_phone.
5. total comes from the same query as the page (COUNT(*) OVER ()), so pagination numbers always agree.
7.6 app/lead/routes.py
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.lead.permissions import CAN_ASSIGN_LEADS
from app.lead.schemas import LeadAssign, LeadCreate, LeadListParams, LeadListResponse, LeadResponse
from app.lead.service import lead_service
from app.user.dependencies import get_current_active_user, require_roles
from app.user.models import User

lead_router = APIRouter(prefix="/leads", tags=["Leads"])


@lead_router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    data: LeadCreate,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await lead_service.create_lead(session, data, current_user)


@lead_router.get("", response_model=LeadListResponse)
async def list_leads(
    params: LeadListParams = Depends(),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    items, total = await lead_service.list_leads(session, params, current_user)
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


@lead_router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await lead_service.get_lead(session, lead_id, current_user)


@lead_router.patch("/{lead_id}/assignee", response_model=LeadResponse)
async def assign_lead(
    lead_id: uuid.UUID,
    data: LeadAssign,
    _: None = Depends(require_roles(*CAN_ASSIGN_LEADS)),
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await lead_service.assign_lead(session, lead_id, data.assigned_to, current_user)
params: LeadListParams = Depends() turns every field of the model into a query parameter (Herbally list standard). Pydantic models are immutable here: never change params, compute new values in the service instead.
7.7 app/lead/__init__.py and the router registry
from .models import Lead, LeadStatusHistory
from .routes import lead_router

__all__ = ["Lead", "LeadStatusHistory", "lead_router"]
In app/apis/v1.py add:
from app.lead import lead_router
...
router.include_router(lead_router)
Check in /docs:
1. As staff: POST /leads without assigned_to. The response shows the staff user as assigned_to.
2. As staff: send assigned_to. Expect 403 assignee_not_allowed.
3. Create another lead with the same phone written differently (98470-12345). Expect 409 duplicate_phone.
4. As admin: create a lead assigned to the demo manager. As staff: GET /leads/{that id}. Expect 404.
5. GET /leads?limit=500: 422 in our error shape. GET /leads?sort_by=name&sort_order=asc: sorted by name.
6. As manager: PATCH /leads/{id}/assignee to an admin's id. Expect 422 invalid_assignee.
Commits: Add lead exceptions, permissions and schemas, Add lead CRUD with filtered pagination, Add create, get and list lead endpoints with staff scope, Add lead assignment
Step 8: Status rules and status history
Goal: PATCH /leads/{id}/status and GET /leads/{id}/status-history. The allowed moves live in one dictionary, and every change writes a history row in the same transaction: two CRUD flushes, one service commit.
Every arrow above is one entry in ALLOWED_TRANSITIONS below; the dashed arrow is the only move staff can't make.
8.1 app/lead/status_rules.py
Why a separate file: the rules are pure data plus one function. No database, no HTTP. You can read the whole pipeline at a glance and unit-test it alone. It is lead business logic, so it lives in app/lead/.
from app.lead.enums import LeadStatus
from app.lead.exceptions import InvalidStatusTransitionException, ReopenNotAllowedException
from app.lead.permissions import CAN_REOPEN_LOST_LEADS
from app.user.enums import Role

S = LeadStatus

ALLOWED_TRANSITIONS: dict[LeadStatus, tuple[LeadStatus, ...]] = {
    S.NEW: (S.CONTACTED, S.LOST),
    S.CONTACTED: (S.QUALIFIED, S.LOST),
    S.QUALIFIED: (S.PROPOSAL_SENT, S.LOST),
    S.PROPOSAL_SENT: (S.WON, S.LOST),
    S.WON: (),                # final
    S.LOST: (S.CONTACTED,),   # reopening: admin or manager only
}

CLOSED_STATUSES = (S.WON, S.LOST)  # no follow-ups on these (Step 9)


def check_transition(current: LeadStatus, target: LeadStatus, role: Role) -> None:
    allowed = ALLOWED_TRANSITIONS[current]
    if target not in allowed:
        raise InvalidStatusTransitionException(current, target, allowed)
    if current == S.LOST and role not in CAN_REOPEN_LOST_LEADS:
        raise ReopenNotAllowedException()
• "Changing" to the same status is rejected automatically, because no status lists itself as allowed.
• new to won gives Can't move a lead from 'new' to 'won'. Allowed: contacted, lost., the brief's message word for word.
• Decision: staff reopening a lost lead returns 403 (their role isn't allowed), not 409. Write this in the README.
8.2 Add to app/lead/schemas.py
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class LeadStatusChange(BaseModel):
    status: LeadStatus
    reason: Reason | None = None


class StatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_status: LeadStatus
    to_status: LeadStatus
    reason: str | None
    changed_by: UserBriefResponse
    changed_at: datetime
(Put LeadStatusChange under Requests and StatusHistoryResponse under Responses.)
8.3 Add to app/lead/service.py
New imports:
from app.lead.crud import lead_status_history_crud
from app.lead.exceptions import LostReasonRequiredException
from app.lead.models import LeadStatusHistory
from app.lead.schemas import LeadStatusChange
from app.lead.status_rules import check_transition
New methods inside LeadService:
    async def change_status(
        self, session: AsyncSession, lead_id: uuid.UUID, data: LeadStatusChange, user: User
    ) -> Lead:
        if data.status == LeadStatus.LOST and not data.reason:
            raise LostReasonRequiredException()

        lead = await self.get_lead(session, lead_id, user, for_update=True)
        check_transition(lead.status, data.status, user.role)

        await lead_status_history_crud.create(session, obj_in={
            "lead_id": lead.id,
            "from_status": lead.status,
            "to_status": data.status,
            "reason": data.reason or None,
            "changed_by_id": user.id,
        })
        lead = await lead_crud.update(session, db_obj=lead, obj_in={"status": data.status})
        await session.commit()  # history row + new status: both saved, or neither
        return lead

    async def get_status_history(
        self, session: AsyncSession, lead_id: uuid.UUID, user: User
    ) -> list[LeadStatusHistory]:
        await self.get_lead(session, lead_id, user)  # access check: 404 for other staff
        return await lead_status_history_crud.list_for_lead(session, lead_id)
Three things to be able to explain in the review:
1. One transaction, the Herbally way. Two CRUD calls each flush() their SQL inside the same open transaction. One commit() in the service makes both permanent. If anything raises before it, the session closes and PostgreSQL rolls back both.
2. Row lock (for_update=True). Two people press "contacted" at the same moment. Without a lock, both read new, both pass the check, and you get two history rows. SELECT ... FOR UPDATE makes the second request wait until the first commits; it then reads contacted and gets a correct 409.
3. Order of checks. Missing reason (422) first, because it is bad input. Then access (404), then the transition (409 or 403).
8.4 Add to app/lead/routes.py
from app.lead.schemas import LeadStatusChange, StatusHistoryResponse


@lead_router.patch("/{lead_id}/status", response_model=LeadResponse)
async def change_status(
    lead_id: uuid.UUID,
    data: LeadStatusChange,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await lead_service.change_status(session, lead_id, data, current_user)


@lead_router.get("/{lead_id}/status-history", response_model=list[StatusHistoryResponse])
async def status_history(
    lead_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await lead_service.get_status_history(session, lead_id, current_user)
Check in /docs: new to won gives 409 with the allowed list. lost without reason gives 422. As staff, move your lead to lost with a reason, then back to contacted: 403. As manager, the same move works. GET /status-history lists every change, oldest first.
Commits: Add lead status transition rules, then Record status history in the same transaction as the status change
Step 9: Follow-ups
Goal: add follow-ups to a lead, list them soonest first, and mark one done. This module reuses what you built: lead_service.get_lead and owner_scope for access, the core exceptions and CRUD, and the migration pattern. Building the same layers a third time is how the pattern sticks.
9.1 app/follow_up/enums.py
from enum import Enum


class FollowUpType(str, Enum):
    CALL = "call"
    MEETING = "meeting"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
9.2 app/follow_up/models.py
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, enum_values
from app.core.utils import utc_now
from app.follow_up.enums import FollowUpType

if TYPE_CHECKING:
    from app.user.models import User


class FollowUp(Base):
    __tablename__ = "follow_ups"
    __table_args__ = (
        Index("ix_follow_ups_lead_id", "lead_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id"), nullable=False)
    type: Mapped[FollowUpType] = mapped_column(Enum(FollowUpType, values_callable=enum_values), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        "created_by", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id], lazy="joined")
Add to app/core/alembic_models_import.py:
# Follow-up module
from app.follow_up.models import FollowUp  # noqa: F401
9.3 Migration
alembic revision --autogenerate -m "create follow ups table"
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

follow_up_type_enum = postgresql.ENUM(
    "call", "meeting", "email", "whatsapp", name="followuptype", create_type=False
)


def upgrade() -> None:
    follow_up_type_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "follow_ups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", follow_up_type_enum, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("is_done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("outcome", sa.String(length=1000), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_follow_ups_lead_id", "follow_ups", ["lead_id"])


def downgrade() -> None:
    op.drop_table("follow_ups")
    follow_up_type_enum.drop(op.get_bind(), checkfirst=True)
alembic upgrade head
9.4 app/follow_up/exceptions.py
from app.core.exceptions import ConflictException, NotFoundException
from app.lead.enums import LeadStatus


class FollowUpNotFoundException(NotFoundException):
    code = "follow_up_not_found"
    message = "Follow-up not found"


class LeadClosedException(ConflictException):
    code = "lead_closed"

    def __init__(self, status: LeadStatus):
        super().__init__(f"Can't add a follow-up to a lead that is {status.value}")


class FollowUpAlreadyDoneException(ConflictException):
    code = "follow_up_already_done"
    message = "This follow-up is already completed"
9.5 app/follow_up/schemas.py
Timezones: AwareDatetime rejects a time with no offset (2026-10-03T10:00:00 is ambiguous). Any offset is accepted, and we convert to UTC before saving. Pydantic writes UTC times with a Z on the way out; the frontend converts to local time for display.
import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, field_validator

from app.follow_up.enums import FollowUpType
from app.user.schemas import UserBriefResponse


# ==================== Requests ====================

class FollowUpCreate(BaseModel):
    type: FollowUpType
    scheduled_at: AwareDatetime
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None

    @field_validator("scheduled_at")
    @classmethod
    def must_be_future(cls, value: datetime) -> datetime:
        value = value.astimezone(timezone.utc)
        if value <= datetime.now(timezone.utc):
            raise ValueError("scheduled_at must be in the future")
        return value


class FollowUpComplete(BaseModel):
    outcome: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


# ==================== Responses ====================

class FollowUpResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    type: FollowUpType
    scheduled_at: datetime
    note: str | None
    is_done: bool
    outcome: str | None
    completed_at: datetime | None
    created_by: UserBriefResponse
    created_at: datetime
9.6 app/follow_up/crud.py
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDBase
from app.follow_up.models import FollowUp
from app.follow_up.schemas import FollowUpComplete, FollowUpCreate
from app.lead.models import Lead


class FollowUpCRUD(CRUDBase[FollowUp, FollowUpCreate, FollowUpComplete]):
    async def list_for_lead(self, session: AsyncSession, lead_id: uuid.UUID) -> list[FollowUp]:
        stmt = (
            select(FollowUp)
            .where(FollowUp.lead_id == lead_id)
            .order_by(FollowUp.scheduled_at, FollowUp.id)
        )
        return list(await session.scalars(stmt))

    async def get_scoped(
        self,
        session: AsyncSession,
        follow_up_id: uuid.UUID,
        *,
        owner_user_ids: list[uuid.UUID] | None,
        for_update: bool = False,
    ) -> FollowUp | None:
        stmt = (
            select(FollowUp)
            .join(Lead, Lead.id == FollowUp.lead_id)
            .where(FollowUp.id == follow_up_id)
        )
        if owner_user_ids is not None:
            stmt = stmt.where(Lead.assigned_to_id.in_(owner_user_ids))
        if for_update:
            stmt = stmt.with_for_update(of=FollowUp).execution_options(populate_existing=True)
        return await session.scalar(stmt)


follow_up_crud = FollowUpCRUD(FollowUp)
9.7 app/follow_up/service.py
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import utc_now
from app.follow_up.crud import follow_up_crud
from app.follow_up.exceptions import (
    FollowUpAlreadyDoneException,
    FollowUpNotFoundException,
    LeadClosedException,
)
from app.follow_up.models import FollowUp
from app.follow_up.schemas import FollowUpComplete, FollowUpCreate
from app.lead.service import lead_service
from app.lead.status_rules import CLOSED_STATUSES
from app.user.models import User


class FollowUpService:
    async def add_follow_up(
        self, session: AsyncSession, lead_id: uuid.UUID, data: FollowUpCreate, user: User
    ) -> FollowUp:
        # Lock the lead so a change to won/lost can't slip in at the same moment.
        lead = await lead_service.get_lead(session, lead_id, user, for_update=True)
        if lead.status in CLOSED_STATUSES:
            raise LeadClosedException(lead.status)

        follow_up = await follow_up_crud.create(session, obj_in={
            "lead_id": lead.id,
            "type": data.type,
            "scheduled_at": data.scheduled_at,
            "note": data.note,
            "created_by_id": user.id,
        })
        await session.commit()
        return follow_up

    async def list_follow_ups(self, session: AsyncSession, lead_id: uuid.UUID, user: User) -> list[FollowUp]:
        await lead_service.get_lead(session, lead_id, user)  # access check
        return await follow_up_crud.list_for_lead(session, lead_id)

    async def complete_follow_up(
        self, session: AsyncSession, follow_up_id: uuid.UUID, data: FollowUpComplete, user: User
    ) -> FollowUp:
        follow_up = await follow_up_crud.get_scoped(
            session, follow_up_id, owner_user_ids=lead_service.owner_scope(user), for_update=True
        )
        if follow_up is None:
            raise FollowUpNotFoundException()
        if follow_up.is_done:
            raise FollowUpAlreadyDoneException()

        follow_up = await follow_up_crud.update(session, db_obj=follow_up, obj_in={
            "is_done": True,
            "outcome": data.outcome,
            "completed_at": utc_now(),
        })
        await session.commit()
        return follow_up


follow_up_service = FollowUpService()
complete_follow_up uses the same scope as leads (lead_service.owner_scope): staff can only complete follow-ups on leads assigned to them, and get 404 otherwise. The lock stops two "complete" clicks from both succeeding. Calling lead_service from here is fine: follow-ups depend on leads, never the other way round, so there's no import cycle.
9.8 app/follow_up/routes.py
These paths start at two places (/leads/... and /follow-ups/...), so this router has no prefix.
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.follow_up.schemas import FollowUpComplete, FollowUpCreate, FollowUpResponse
from app.follow_up.service import follow_up_service
from app.user.dependencies import get_current_active_user
from app.user.models import User

follow_up_router = APIRouter(tags=["Follow-ups"])


@follow_up_router.post(
    "/leads/{lead_id}/follow-ups", response_model=FollowUpResponse, status_code=status.HTTP_201_CREATED
)
async def add_follow_up(
    lead_id: uuid.UUID,
    data: FollowUpCreate,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await follow_up_service.add_follow_up(session, lead_id, data, current_user)


@follow_up_router.get("/leads/{lead_id}/follow-ups", response_model=list[FollowUpResponse])
async def list_follow_ups(
    lead_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await follow_up_service.list_follow_ups(session, lead_id, current_user)


@follow_up_router.patch("/follow-ups/{follow_up_id}/complete", response_model=FollowUpResponse)
async def complete_follow_up(
    follow_up_id: uuid.UUID,
    data: FollowUpComplete,
    current_user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    return await follow_up_service.complete_follow_up(session, follow_up_id, data, current_user)
9.9 app/follow_up/__init__.py and the router registry
from .models import FollowUp
from .routes import follow_up_router

__all__ = ["FollowUp", "follow_up_router"]
Final app/apis/v1.py:
from fastapi import APIRouter

from app.follow_up import follow_up_router
from app.lead import lead_router
from app.user import auth_router, user_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(user_router)
router.include_router(lead_router)
router.include_router(follow_up_router)
Check in /docs: a scheduled_at in the past gives 422. A follow-up on a lost lead gives 409 lead_closed. Completing the same follow-up twice gives 409. Send "scheduled_at": "2026-12-03T10:00:00+05:30" and the response shows "2026-12-03T04:30:00Z".
Commits: Add follow-up model and migration, then Add follow-up endpoints
Step 10: Tests with pytest and httpx
Goal: automated proof of every rule the reviewers will try. Tests run against a separate crm_test database built with the same Alembic migrations (still no create_all) and emptied before each test. The pytest settings are already in pyproject.toml (Step 0).
10.1 tests/conftest.py
Read the top carefully: the environment variables must be set before anything from app is imported, because get_settings() reads them the first time it runs.
import os
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv()
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://crm:crm@localhost:5432/crm_test")
assert "test" in TEST_URL, "Refusing to run tests against a non-test database"
os.environ["DATABASE_URL"] = TEST_URL
os.environ["TESTING"] = "true"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.main import app  # noqa: E402
from app.user.auth_management.security import create_access_token, hash_password  # noqa: E402
from app.user.enums import Role  # noqa: E402
from app.user.models import User  # noqa: E402

PASSWORD = "Password123!"


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
async def clean_tables():
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE follow_ups, lead_status_history, leads, users CASCADE"))


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


class Actor(NamedTuple):
    user: User
    headers: dict[str, str]


async def make_actor(role: Role, email: str, is_active: bool = True) -> Actor:
    async with SessionLocal() as session:
        user = User(
            full_name=email.split("@")[0],
            email=email,
            password_hash=await hash_password(PASSWORD),
            role=role,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
    return Actor(user, {"Authorization": f"Bearer {create_access_token(user.id)}"})


@pytest.fixture
async def admin() -> Actor:
    return await make_actor(Role.ADMIN, "admin@test.com")


@pytest.fixture
async def manager() -> Actor:
    return await make_actor(Role.MANAGER, "manager@test.com")


@pytest.fixture
async def staff() -> Actor:
    return await make_actor(Role.STAFF, "staff@test.com")


@pytest.fixture
async def other_staff() -> Actor:
    return await make_actor(Role.STAFF, "other@test.com")
python-dotenv comes with pydantic-settings, so load_dotenv needs no extra install.
10.2 tests/helpers.py
def assert_error(response, status: int, code: str | None = None) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    if code is not None:
        assert body["error"]["code"] == code
    return body["error"]


async def create_lead(client, headers, phone="9847012345", **extra) -> dict:
    body = {"name": "Rahul Menon", "phone": phone, "source": "website", **extra}
    response = await client.post("/leads", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def set_status(client, headers, lead_id, status, reason=None):
    body = {"status": status} if reason is None else {"status": status, "reason": reason}
    return await client.patch(f"/leads/{lead_id}/status", json=body, headers=headers)
assert_error checks the error shape every time, so you get the "one error shape everywhere" check for free. Tests send the API's lowercase values ("website", "lost"), never the Python member names.
10.3 tests/test_auth.py
from app.user.auth_management.security import create_refresh_token
from tests.conftest import PASSWORD
from tests.helpers import assert_error


async def test_login_is_case_insensitive_and_me_hides_password(client, admin):
    r = await client.post("/auth/login", json={"email": "ADMIN@test.com", "password": PASSWORD})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert "password_hash" not in me.json()


async def test_wrong_password_and_unknown_email_get_same_message(client, admin):
    wrong = await client.post("/auth/login", json={"email": "admin@test.com", "password": "nope-nope"})
    unknown = await client.post("/auth/login", json={"email": "ghost@test.com", "password": "nope-nope"})
    assert assert_error(wrong, 401)["message"] == assert_error(unknown, 401)["message"]


async def test_no_token_is_401(client):
    assert_error(await client.get("/leads"), 401)


async def test_refresh_token_cannot_be_used_as_access_token(client, staff):
    headers = {"Authorization": f"Bearer {create_refresh_token(staff.user.id)}"}
    assert_error(await client.get("/auth/me", headers=headers), 401)


async def test_deactivated_user_token_stops_working(client, admin, staff):
    assert (await client.get("/auth/me", headers=staff.headers)).status_code == 200
    r = await client.patch(f"/users/{staff.user.id}/active", json={"is_active": False}, headers=admin.headers)
    assert r.status_code == 200
    assert_error(await client.get("/auth/me", headers=staff.headers), 401)


async def test_manager_cannot_create_users_and_staff_cannot_list(client, manager, staff):
    body = {"full_name": "X", "email": "x@test.com", "password": "Password123!", "role": "staff"}
    assert_error(await client.post("/users", json=body, headers=manager.headers), 403)
    assert_error(await client.get("/users", headers=staff.headers), 403)
10.4 tests/test_leads.py (3 of the 5 required tests)
import asyncio

from tests.helpers import assert_error, create_lead


async def test_staff_lead_is_assigned_to_them(client, staff):
    lead = await create_lead(client, staff.headers)
    assert lead["assigned_to"]["id"] == str(staff.user.id)
    assert lead["status"] == "new"
    assert lead["phone"] == "+919847012345"


async def test_staff_cannot_see_another_users_lead(client, manager, staff, other_staff):  # REQUIRED
    lead = await create_lead(client, manager.headers, assigned_to=str(other_staff.user.id))
    assert_error(await client.get(f"/leads/{lead['id']}", headers=staff.headers), 404)
    listed = (await client.get("/leads", headers=staff.headers)).json()
    assert listed["total"] == 0


async def test_staff_cannot_assign_a_lead(client, staff, other_staff):  # REQUIRED
    lead = await create_lead(client, staff.headers)
    r = await client.patch(
        f"/leads/{lead['id']}/assignee", json={"assigned_to": str(other_staff.user.id)}, headers=staff.headers
    )
    assert_error(r, 403)


async def test_staff_sending_assigned_to_on_create_is_403(client, staff):
    body = {"name": "A", "phone": "9847012345", "source": "website", "assigned_to": str(staff.user.id)}
    assert_error(await client.post("/leads", json=body, headers=staff.headers), 403, "assignee_not_allowed")


async def test_duplicate_phone_in_any_format_is_409(client, manager):  # REQUIRED
    await create_lead(client, manager.headers, phone="98470 12345")
    for same_number in ["9847012345", "+91 98470-12345"]:
        r = await client.post(
            "/leads", json={"name": "B", "phone": same_number, "source": "referral"}, headers=manager.headers
        )
        assert_error(r, 409, "duplicate_phone")


async def test_duplicate_phone_sent_at_the_same_time(client, manager):
    body = {"name": "Race", "phone": "9847000000", "source": "website"}
    r1, r2 = await asyncio.gather(
        client.post("/leads", json=body, headers=manager.headers),
        client.post("/leads", json=body, headers=manager.headers),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]


async def test_reassign_hides_lead_from_old_assignee(client, manager, staff, other_staff):
    lead = await create_lead(client, staff.headers)
    r = await client.patch(
        f"/leads/{lead['id']}/assignee", json={"assigned_to": str(other_staff.user.id)}, headers=manager.headers
    )
    assert r.status_code == 200
    assert_error(await client.get(f"/leads/{lead['id']}", headers=staff.headers), 404)


async def test_assign_to_admin_is_422(client, admin, manager):
    lead = await create_lead(client, manager.headers)
    r = await client.patch(
        f"/leads/{lead['id']}/assignee", json={"assigned_to": str(admin.user.id)}, headers=manager.headers
    )
    assert_error(r, 422, "invalid_assignee")


async def test_pagination_total_sorting_and_limit_cap(client, manager):
    for i, name in enumerate(["Charlie", "Alice", "Bob"]):
        await create_lead(client, manager.headers, phone=f"98470{i:05d}", name=name)
    page = (await client.get("/leads?limit=2&offset=0", headers=manager.headers)).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    by_name = (await client.get("/leads?sort_by=name&sort_order=asc", headers=manager.headers)).json()
    assert [lead["name"] for lead in by_name["items"]] == ["Alice", "Bob", "Charlie"]
    assert_error(await client.get("/leads?limit=500", headers=manager.headers), 422)
10.5 tests/test_status.py (1 required test)
from tests.helpers import assert_error, create_lead, set_status


async def test_new_to_won_is_409_with_allowed_list(client, manager):  # REQUIRED
    lead = await create_lead(client, manager.headers)
    error = assert_error(await set_status(client, manager.headers, lead["id"], "won"), 409,
                         "invalid_status_transition")
    assert "contacted, lost" in error["message"]


async def test_lost_without_reason_is_422(client, manager):
    lead = await create_lead(client, manager.headers)
    assert_error(await set_status(client, manager.headers, lead["id"], "lost"), 422)


async def test_staff_cannot_reopen_but_manager_can(client, manager, staff):
    lead = await create_lead(client, staff.headers)
    assert (await set_status(client, staff.headers, lead["id"], "lost", "No budget")).status_code == 200
    assert_error(await set_status(client, staff.headers, lead["id"], "contacted"), 403, "reopen_not_allowed")
    assert (await set_status(client, manager.headers, lead["id"], "contacted")).status_code == 200


async def test_every_change_is_in_history(client, manager):
    lead = await create_lead(client, manager.headers)
    await set_status(client, manager.headers, lead["id"], "contacted")
    await set_status(client, manager.headers, lead["id"], "lost", "Went with a competitor")
    history = (await client.get(f"/leads/{lead['id']}/status-history", headers=manager.headers)).json()
    assert [(h["from_status"], h["to_status"]) for h in history] == [("new", "contacted"), ("contacted", "lost")]
    assert history[1]["reason"] == "Went with a competitor"
10.6 tests/test_follow_ups.py (1 required test)
from datetime import datetime, timedelta, timezone

from tests.helpers import assert_error, create_lead, set_status


def future(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def test_follow_up_on_lost_lead_is_409(client, manager):  # REQUIRED
    lead = await create_lead(client, manager.headers)
    await set_status(client, manager.headers, lead["id"], "lost", "Not interested")
    r = await client.post(f"/leads/{lead['id']}/follow-ups", json={"type": "call", "scheduled_at": future()},
                          headers=manager.headers)
    assert_error(r, 409, "lead_closed")


async def test_follow_up_in_the_past_is_422(client, manager):
    lead = await create_lead(client, manager.headers)
    r = await client.post(f"/leads/{lead['id']}/follow-ups", json={"type": "call", "scheduled_at": future(-1)},
                          headers=manager.headers)
    assert_error(r, 422)


async def test_completing_twice_is_409(client, staff):
    lead = await create_lead(client, staff.headers)
    fu = (await client.post(f"/leads/{lead['id']}/follow-ups", json={"type": "call", "scheduled_at": future()},
                            headers=staff.headers)).json()
    body = {"outcome": "Interested, wants a demo"}
    assert (await client.patch(f"/follow-ups/{fu['id']}/complete", json=body, headers=staff.headers)).status_code == 200
    assert_error(await client.patch(f"/follow-ups/{fu['id']}/complete", json=body, headers=staff.headers), 409,
                 "follow_up_already_done")
10.7 tests/test_phone_and_rules.py (plain unit tests, no database needed)
import pytest

from app.lead.enums import LeadStatus
from app.lead.exceptions import InvalidStatusTransitionException, ReopenNotAllowedException
from app.lead.phone import normalize_phone
from app.lead.status_rules import check_transition
from app.user.enums import Role


@pytest.mark.parametrize("raw", ["98470 12345", "9847012345", "+91 98470 12345", "+91-98470-12345"])
def test_same_number_in_every_format(raw):
    assert normalize_phone(raw, "91") == "+919847012345"


@pytest.mark.parametrize("raw", ["12345", "++919847012345", "98470abcde", "1234567890123456"])
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw, "91")


def test_same_status_is_not_a_valid_move():
    with pytest.raises(InvalidStatusTransitionException):
        check_transition(LeadStatus.NEW, LeadStatus.NEW, Role.ADMIN)


def test_staff_reopen_is_refused():
    with pytest.raises(ReopenNotAllowedException):
        check_transition(LeadStatus.LOST, LeadStatus.CONTACTED, Role.STAFF)
Run:
pytest -v
If a test fails, read the response.text printed by the assert: it is our error JSON and usually says exactly what went wrong.
Commit: Add tests for auth, access rules, status moves, duplicates and follow-ups
Step 11: README, decisions, known gaps and review prep
Goal: someone who has never seen the project clones it and succeeds by following the README alone. Test this yourself: clone into a new folder and follow your own README word for word.
11.1 README.md template
# CRM Leads API

FastAPI + PostgreSQL backend for a small CRM leads module: JWT auth, role-based access
(admin / manager / staff), leads with a status pipeline and history, and follow-ups.
Structured to the Herbally backend conventions (app/core + feature modules,
Route → Service → CRUD → Model).

## Setup

Prerequisites: Python 3.11+, Docker, uv.

```bash
git clone https://github.com/<you>/crm-leads-api.git && cd crm-leads-api
uv sync && source .venv/bin/activate
cp .env.example .env          # then set JWT_SECRET_KEY (32+ chars), ADMIN_EMAIL, ADMIN_PASSWORD
docker compose up -d
docker compose exec postgres createdb -U crm crm_test
alembic upgrade head
python -m scripts.seed        # same as: python -m app.user.seed
uvicorn app.core.main:app --reload
```

Every variable is documented in `.env.example`.

## Tests

```bash
pytest -v
```

## Try it

1. Open http://127.0.0.1:8000/docs
2. `POST /api/v1/auth/login` with ADMIN_EMAIL / ADMIN_PASSWORD, copy `access_token`
3. Click Authorize, paste the token
4. `GET /api/v1/auth/me`

## Project layout

- `app/core/` reusable infrastructure: settings, database, exceptions, logging, generic CRUD
- `app/user/`, `app/lead/`, `app/follow_up/` feature modules
- `app/apis/v1.py` router registry; `migrations/` Alembic; `tests/`

## Decisions
(see table below)

## Known gaps
(see list below)
11.2 Decisions to write down (copy the ones you implemented)
Topic
What we chose
Why
Project structure
Herbally conventions: app/core, feature modules, Route → Service → CRUD → Model
Team standard; the brief's layout is only a suggestion
Brief vs conventions
Where they disagree, the brief wins
The brief defines the graded behaviour
Exceptions
Module exceptions subclass our AppException, not HTTPException
The brief forbids HTTPException in services
Access control
Three fixed roles with require_roles(...); role sets in each module's permissions.py
The brief's data model has one role column, no permission table
Lead scope
Service computes owner_user_ids; CRUD filters by it
Brief: ownership checks belong in the service
Seed
app/user/seed.py, with scripts/seed.py as the entry point; not run at startup
Brief requires scripts/seed.py
Phone format
10 digits without + get +91; everything stored as + and digits. +91 98470 12345 = 9847012345
The team works in India; one stored form makes the unique constraint meaningful
limit over 100
Rejected with 422
Silent capping hides client bugs
Sorting
sort_by (created_at, updated_at, name, status) + sort_order; default newest first
Herbally list standard; brief's default order kept
Deactivating users
Added PATCH /users/{id}/active (admin only); admins can't deactivate themselves
The brief requires deactivation but lists no endpoint
Staff reopening a lost lead
403 reopen_not_allowed
It is a role restriction, not an invalid move
Staff sends assigned_to on create
403 even if it is their own id or null
Brief says sending it is forbidden
Lead creation
No history row; history records changes only
from_status would have no value
Naive datetimes
scheduled_at without an offset is rejected (422)
A time without a timezone is ambiguous
Completing a follow-up on a won/lost lead
Allowed
Brief only blocks adding them
Roles in the JWT
Not stored; user loaded from DB on every request
Deactivation and role changes apply immediately
Refresh tokens
Stateless, not rotated; refresh checks is_active
Keeps scope small
Failed login
Same 401 message for unknown email, wrong password, inactive user; dummy hash check for unknown emails
Doesn't reveal which emails exist
updated_at on users and follow-ups
Added
Herbally model standard; extra columns don't break the brief
lead_status_history table name
Kept singular
The brief names it; Herbally prefers plural
11.3 Known gaps (be honest; it earns points)
• No token revocation or logout; a stolen refresh token works until it expires (unless the user is deactivated).
• No rate limiting on login.
• search doesn't escape % and _, so they act as wildcards.
• Leads assigned to a user who is later deactivated stay assigned to them.
• Herbally pieces not used here: permission tables, Celery, Redis cache, object storage, Prometheus metrics, soft delete.
• Optional extras (API Dockerfile, GET /follow-ups?due=today) not built. (Update this if you build them.)
11.4 Commit history you should end up with
1. Set up project skeleton, pyproject and Postgres compose file
2. Add core settings, database session, base model, utc_now and logging
3. Add core exceptions, global handlers, list params and generic CRUD helpers
4. Add user model and first migration
5. Add user schemas, CRUD and auth exceptions
6. Add JWT login, refresh, me and role guard
7. Add app factory, middleware and v1 router registry
8. Add create, list and deactivate user endpoints
9. Add idempotent user seed and scripts/seed.py entry point
10. Add lead models and migration
11. Add phone normalisation
12. Add lead exceptions, permissions and schemas
13. Add lead CRUD with filtered pagination
14. Add create, get and list lead endpoints with staff scope
15. Add lead assignment
16. Add lead status transition rules
17. Record status history in the same transaction as the status change
18. Add follow-up model and migration
19. Add follow-up endpoints
20. Add tests for auth, access rules, status moves, duplicates and follow-ups
21. Write README with setup, decisions and known gaps
11.5 Review call prep: questions you should answer without notes
• Walk a PATCH /leads/{id}/status request from the route to the database and back. Which layer does what, and which one commits?
• Why does staff get 404, not 403, for someone else's lead? Where is the scope decided, and where is it applied?
• What happens if two POST /leads with the same phone arrive at once? Why doesn't if exists: raise work? Why does the error appear at flush()?
• What does with_for_update protect against in change_status?
• Why is password hashing wrapped in asyncio.to_thread?
• How does a deactivated user's token stop working, when JWTs can't be "cancelled"?
• Why the type claim in tokens?
• Why are enum types created by hand in the migrations, and why values_callable?
• Why does require_roles live in app/user/ and not app/core/?
• How does paginated_select get the total and the page in one query?
• Likely live change: "Only managers can move a lead to won." Answer: add CAN_WIN_LEADS = (Role.MANAGER,) to app/lead/permissions.py, one extra check in check_transition, a new exception, and a test.
Commit: Write README with setup, decisions and known gaps
Acceptance checklist and what comes next
Tick each item yourself through /docs or a test before you submit. The step that builds it is in brackets.
Setup
[ ] A fresh clone runs by following only the README (11)
[ ] alembic upgrade head creates every table on an empty database (3, 6, 9)
[ ] The seed script creates an admin who can log in (5)
Auth and roles
[ ] No token gets 401; a refresh token used as an access token gets 401 (4)
[ ] A deactivated user's existing token stops working (4, 5)
[ ] Manager can't create users (403); staff can't list users (403) (5)
Leads
[ ] Staff create a lead and it is assigned to them automatically (7)
[ ] 98470 12345, 9847012345 and +91 98470 12345 are the same number; decision in README (6, 11)
[ ] Duplicate phone gives 409, even for two requests at the same time (7, 10)
[ ] Staff see only their own leads in the list, and get 404 for anyone else's (7)
[ ] Pagination total is right; limit=500 is rejected (7)
Assignment
[ ] Manager reassigns a lead; the old assignee no longer sees it (7)
[ ] Assigning to an inactive user or an admin gives 422 (7)
Status
[ ] new to won gives 409 with the allowed next statuses in the message (8)
[ ] lost without a reason gives 422 (8)
[ ] Staff can't reopen a lost lead; a manager can (8)
[ ] Every successful change appears in the status history (8)
Follow-ups
[ ] A follow-up in the past gives 422 (9)
[ ] A follow-up on a won or lost lead gives 409 (9)
[ ] Completing a follow-up twice gives 409 (9)
Errors
[ ] Every error, including 422s, uses the {"error": {code, message, details}} shape (2, 10)
[ ] No response contains password_hash, a stack trace or raw SQL (2, 4)
Next: the frontend
The trainee brief itself says API only, so the frontend is extra practice once the backend is submitted. It will follow the same structure you just learned, one folder per feature:
Backend piece
Frontend equivalent
app/user, app/lead, app/follow_up modules
features/auth, features/users, features/leads, features/follow-ups
service.py (business calls)
api/*.ts: one typed function per endpoint
schemas.py (LeadResponse, UserResponse, ...)
TypeScript types with the same names and fields
app/user/dependencies.py + permissions.py (current user, role sets)
An auth context holding the token, plus a role-based route guard using the same role sets
app/core/exceptions.py (one error shape)
One API client that reads error.code / error.message / error.details everywhere
LeadListParams (limit, offset, sort_by, filters)
A list-query hook that keeps those params in the URL
When the backend is done, tell me which frontend stack your team uses (for example React + Vite + TypeScript) and we'll write that guide the same way: step by step, with the code for each step for you to type.