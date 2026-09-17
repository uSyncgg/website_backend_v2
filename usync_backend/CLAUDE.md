# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Contributor Workflow (read this first)

**Deployment pipeline:** work flows `feature/* → staging_environment → main`. `staging_environment` is the **staging** branch; `main` is **production** (the live API). Contributors only ever go as far as staging — their PRs merge into `staging_environment`. Promoting staging to production (`main`) is done separately by Matthew (contact@usync.gg / GitHub `MOconnorUS`) and is **not** part of this loop.

1. **Branch first — always.** Never make edits on `main` or `staging_environment`. At the start of any new feature, create a branch named `feature/<short-kebab-description>`. A PreToolUse hook (`.claude/hooks/guard-branch.sh`) blocks edits on both protected branches for everyone except the owner (identified by `git config user.email`), so this is required, not optional.
2. **Stay up to date.** A SessionStart hook (`.claude/hooks/sync-staging.sh`) checks whether the feature branch is behind `origin/staging_environment` and, if so, tells Claude to proactively merge it in (resolving any conflicts in plain language) before doing other work. The `/new-pr` skill repeats this check right before opening the PR.
3. **Build the feature**, following the layering and conventions described elsewhere in this file (router → service → model, absolute imports, `app/models/__init__.py` re-exports, etc.).
4. **Open a PR** with the `new-pr` skill, which fills the uSync backend PR template (summary, affected endpoints/services, database changes, env vars) and opens the pull request **into `staging_environment`** (staging) — never `main`.
5. **Review gate.** Matthew reviews every PR and either **Approves** (he merges to `staging_environment`) or **Requests changes**. Contributors must NOT merge their own PRs, and must NOT open PRs against `main` — only Matthew does that, through the owner-only `pr-to-main` skill (checked against his `git config user.email`).
6. **Address feedback** by reading the PR review comments (`gh pr view --comments`) and pushing fixes to the same branch; this re-triggers review.
7. **After merge,** delete the feature branch and start the loop again from step 1. Once changes are verified on staging, Matthew promotes `staging_environment` to `main` in a separate, owner-only production PR.

See `COWORKER-GUIDE.md` for the plain-English version of this loop.

## Overview

Async FastAPI backend for the usync.gg esports platform. It serves tournament data, handles host/event form submissions, user registration/verification, and Stripe payments. Persistence is a Supabase Postgres database accessed through async SQLAlchemy 2.0; schema changes are managed with Alembic.

## Commands

The project uses `uv` (Python 3.12).

```bash
uv sync                                    # install/refresh dependencies from uv.lock
uv run uvicorn main:app --reload           # run the dev server (http://127.0.0.1:8000, docs at /docs)

# Alembic migrations (run from repo root)
uv run alembic revision --autogenerate -m "message"   # generate migration from model changes
uv run alembic upgrade head                            # apply migrations
uv run alembic downgrade -1                            # roll back one revision
```

There is currently no test suite, linter, or formatter configured.

## Two database URLs — do not mix them up

The app and Alembic use **different** connection strings and drivers:

- Runtime (`app/core/db.py`) uses `ASYNC_SUPABASE_CONNECTION_URL` with the **asyncpg** driver.
- Alembic (`alembic/env.py`) uses `SUPABASE_CONNECTION_URL` with the synchronous **psycopg2** driver.

The async engine sets `statement_cache_size=0` — this is required to work through Supabase's connection pooler (pgbouncer) and should not be removed.

## Architecture

Request flow is layered: **router → service → model**, with **schema** (Pydantic) types validating input/output at the router boundary.

- `main.py` — app factory: CORS (allows localhost:3000 and usync.gg), the `add_process_time` HTTP middleware, a `lifespan` that builds the Stripe client onto `app.state.stripe` and disposes the DB engine on shutdown, and router registration via `app.include_router`.
- `app/routers/` — thin HTTP handlers. They declare the route, depend on `get_db` / `get_stripe_client`, and delegate to a service. Keep business logic out of routers.
- `app/services/` — business logic. Receives the `AsyncSession` (or Stripe client) and does the work. `app/services/__init__.py` holds shared constants (e.g. Stripe currency/payment-method types).
- `app/models/` — SQLAlchemy 2.0 declarative models (`Mapped[...]` / `mapped_column`). All models inherit from `Base` in `app/models/base.py`. `app/models/__init__.py` re-exports every model — **Alembic autogenerate only sees models imported here**, so add new models to this file or migrations will miss them.
- `app/schemas/` — Pydantic request/response models referenced by routers as `response_model`.
- `app/core/` — `db.py` (engine + `AsyncSessionLocal`), `dependencies.py` (`get_db` yields a session, `get_stripe_client` pulls from `app.state`), `middleware.py`.

### Dependency injection conventions

- DB access: inject `db: AsyncSession = Depends(get_db)` and pass it into the service.
- Stripe: inject `stripeClient: StripeClient = Depends(get_stripe_client)` — never construct a Stripe client inside a handler; it lives on `app.state`.

### Data model patterns

- **Parent/child table split**: a generic parent table (e.g. `tournaments_parent`, keyed by game) is joined to game-specific child tables (e.g. `cod_tournaments`) via a shared `id` foreign key. The same pattern recurs for users, event sites, statistics, and forms.
- **Game-keyed dispatch**: services map a game string to its model via a dict like `GAME_MODELS = {"cod": CodTournament}` and return `[]` for unknown games. Extend these maps when adding a game rather than branching.
- COD tournament rows carry many boolean `is_*` flags (region/platform/skill/price filters) alongside their human-readable string fields.
- Tournament queries filter on `starts_at` using timezone-aware `datetime.now(ZoneInfo("America/New_York"))`.

## Current state / gotchas

- Only the `tournaments` router is wired into `main.py`. The `payment`, `webhooks`, `event_forms`, `host_forms`, `users`, and `verification` routers exist but are **not yet registered**, and most of their service functions are stubs (`# Need return function`). Wire the router into `main.py` and implement the service when building these out.
- Import paths should be absolute from the package root (`from app.services...`). A few stub files use bare imports (`from services.webhooks import ...`) which will fail at import time — fix to `app.services...` when activating them.
- Stripe currently uses the **test** key/webhook secret (`STRIPE_TEST_KEY`, `STRIPE_TEST_WEBHOOK_KEY`). Live keys exist in env but are not used yet.

## Environment variables (`.env`, not committed)

`SUPABASE_CONNECTION_URL`, `ASYNC_SUPABASE_CONNECTION_URL`, `STRIPE_KEY`, `STRIPE_TEST_KEY`, `STRIPE_TEST_WEBHOOK_KEY`, `STRIPE_LIVE_WEBHOOK_KEY`, `ZOHO_EMAIL`, `ZOHO_PASSWORD` (Zoho creds back transactional email via `fastapi-mail`).
