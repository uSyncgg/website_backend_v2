---
name: pr-to-main
description: Owner-only. Open a pull request from any branch directly into main (production). Use when Matthew says to "PR into main", "promote to production", or "ship straight to prod". Not for regular contributors — their work goes through new-pr into staging_environment.
---

# Open a Pull Request directly into main — OWNER ONLY

This skill bypasses the normal `feature → staging_environment (staging) → main (production)` gate and opens a PR **directly into `main`**, from whatever branch is currently checked out. It is reserved for the repo owner, Matthew (`MOconnorUS` / `oconnormattc@gmail.com`). Regular contributors must use the `new-pr` skill, which targets `staging_environment`.

## 0. Access guard — verify the operator is the owner

Run:

```bash
git config user.email
```

If the result is **not** `oconnormattc@gmail.com`:
- Stop immediately. Do not pull, merge, push, or open a PR.
- Tell the person: "This skill is owner-only. Contributor work is promoted to production by Matthew after it has been verified on `staging_environment`. Use the `new-pr` skill to open a PR into staging instead."
- End here.

Only continue past this point if the email matches exactly.

## 1. Make sure local work is committed

- Run `git rev-parse --abbrev-ref HEAD` and note the current branch — this is the branch being promoted (often `staging_environment`, but this skill works from any branch).
- Run `git status`. If there are uncommitted changes, confirm with Matthew what they are, then stage and commit with a clear message.

## 1a. Pull everything from the branch

- If the branch already has an upstream (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds), run `git pull origin <branch>` to bring in anything pushed from elsewhere before continuing. If there's no upstream yet, skip this.
- If the pull produces conflicts, resolve them carefully and explain what was reconciled.

## 1b. Merge main into the branch (no surprises at merge time)

- Run `git fetch origin main`, then `git merge origin/main`.
- If it merges cleanly, confirm the branch is current with `main`.
- If there are conflicts, resolve them carefully and explain what was reconciled.
- Only proceed once the branch is up to date with `main` and conflict-free.

## 1c. Push everything

- Push the branch to `origin` with upstream tracking (`git push -u origin <branch>`), so the PR includes every commit, the pulled work, and the main merge.

## 2. Open the PR into main

```bash
gh pr create --base main --title "<concise title>" --body "<filled template below>"
```

If `gh` is unavailable, push the branch and provide the GitHub "compare & pull request" URL with the filled body ready to paste.

### PR body template (fill every field)

```markdown
## Summary
<1–2 sentence summary of what is being shipped to production.>

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

## Promotion notes
- Source branch: <branch being promoted, e.g. staging_environment>
- Verified on staging: <yes/no — note what was checked>
```

## 3. After opening

- Give Matthew the PR link.
- Note that this PR targets **`main` (production)** — merging it ships to prod.
