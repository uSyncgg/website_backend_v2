---
name: new-pr
description: Open a pull request for the current work using the uSync backend PR template. Use when a contributor says they are done with a feature and wants to submit it for review (e.g. "make a PR", "submit this for review", "open a pull request").
---

# Open a Pull Request (uSync backend template)

Follow these steps in order. Be clear about what you're doing at each step and never assume the contributor has git experience beyond the basics.

**Where PRs go:** Feature work is merged into the **`staging_environment`** branch. Staging is verified there before it is later promoted to `main` (production). So every PR this skill opens targets `staging_environment` as its base — never `main` directly. Only Matthew promotes `staging_environment` to `main`, and only he can do so.

## 1. Safety check: pick a valid source branch

Run `git rev-parse --abbrev-ref HEAD`. Then:
- If the branch is `main` or `master`: stop. Tell the contributor we never submit work directly from the production branch. Offer to move their changes onto a new feature branch named `feature/<short-kebab-description>` and continue from there.
- If the branch is `staging_environment`: stop. That is the staging branch we merge *into*, so we can't open a PR from it into itself. Offer to move their changes onto a new `feature/<short-kebab-description>` branch and continue from there.

## 2. Make sure local work is committed

- Run `git status`. If there are uncommitted changes, stage and commit them with a clear, descriptive message.

## 2a. Pull everything from the branch

- If the branch already has an upstream (was pushed before, `git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds), run `git pull origin <branch>` to bring in anything pushed from elsewhere before continuing. If there's no upstream yet (brand-new branch), skip this — there's nothing to pull.
- If the pull produces conflicts, resolve them carefully, explain what you reconciled, and confirm anything ambiguous with the contributor before moving on.

## 2b. Merge staging_environment into the branch (no surprises at merge time)

- Run `git fetch origin staging_environment`, then `git merge origin/staging_environment`.
- If it merges cleanly, briefly confirm the branch is current.
- If there are conflicts, resolve them carefully, explain what you reconciled, and confirm anything ambiguous with the contributor.
- Only proceed once the branch is up to date with staging and conflict-free.

## 2c. Push everything

- Push the branch to `origin` with upstream tracking (`git push -u origin <branch>`), so the PR includes the commits, the pulled work, and the staging merge.

## 3. Gather the required template fields

Before opening the PR, fill in every section of the template below. Do not leave placeholders.

- **Summary + what changed** — A one or two sentence summary, followed by a bullet list of the concrete changes (routers, services, models, schemas touched).
- **Affected endpoints / services** — Inspect the diff and `app/routers/` to list every API route this change adds or touches (e.g. `POST /tournaments/cod`). If it's a service-only or model-only change with no route impact, say so explicitly (e.g. "None (internal service/model change)").
- **Database changes** — Check `alembic/versions/` for any new migration in the diff, and `app/models/` for schema changes. If a new model was added, confirm it was also added to `app/models/__init__.py` (per this repo's `CLAUDE.md` — autogenerate only sees models re-exported there). If there's a migration, note whether it was run locally (`uv run alembic upgrade head`).
- **Environment variables** — Note any new/changed variables the change depends on (see the env var list in `CLAUDE.md`). If none, say "None."

## 4. Open the PR

Use the GitHub CLI:

```bash
gh pr create --base staging_environment --title "<concise title>" --body "<filled template below>"
```

If `gh` is not available, fall back to `git push` and give the contributor the GitHub "compare & pull request" URL to click, with the filled template ready to paste.

### PR body template (fill every field)

```markdown
## Summary
<1–2 sentence summary of the change.>

### What changed
- <change 1>
- <change 2>

## Affected endpoints / services
- <endpoint or service 1>
- <endpoint or service 2>

## Database changes
- Migration added: <yes/no — filename if yes>
- Model changes: <describe, or "None">

## Environment variables
- <new/changed var, or "None">

## For reviewer (@matthew)
- [ ] I started this from a fresh feature branch
- [ ] I described what changed and which endpoints/services it affects above
- [ ] Any new Alembic migration is included and was tested locally
```

## 5. After opening

- Give the contributor the PR link.
- Remind them this PR merges into **`staging_environment`** (staging), not production. After it lands and is verified on staging, staging is promoted to `main` separately, by Matthew only.
- Remind them: Matthew will review and either **Approve** (he merges to staging) or **Request changes** (they tell Claude "address the PR review comments" and we push fixes).
- Remind them NOT to merge themselves, and NOT to open a PR against `main` — only Matthew does that (via the owner-only `pr-to-main` flow).
