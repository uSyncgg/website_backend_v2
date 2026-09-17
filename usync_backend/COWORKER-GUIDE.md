# Contributing to the uSync backend — plain-English guide

You talk to Claude; it handles the git mechanics. Follow these five steps every time you work on something.

## Where you work
Open **https://claude.ai/code** and pick the `website_backend_v2` repository.

---

## The 5 steps

### 1. Start a branch (always do this first)
Tell Claude:
> "Start a new branch called feature/<a-few-words-about-what-im-doing>"

Example: *"Start a new branch called feature/add-halo-tournament-support"*

⚠️ You can't edit `main` (production) or `staging_environment` directly — Claude will refuse and ask you to make a branch. That's by design.

> **Heads up:** Each time you open a session on your branch, Claude automatically checks whether staging has moved since you last worked, and updates your branch to match. If it asks you about a "conflict," just answer its questions in plain English — it's making sure two changes don't clash.

### 2. Describe what you want
Just explain it in normal words:
> "Add a new router for Halo tournament submissions, following the same pattern as the CoD tournaments router."

Claude will make the changes. Ask it to run the dev server (`uv run uvicorn main:app --reload`) if you want to see it working, or to walk you through what it touched.

### 3. Submit it for review
When you're happy, tell Claude:
> "/new-pr"

Claude fills out the form (what changed, which endpoints/services are affected, any database migration, any new env vars) and creates the **pull request** — that's the thing Matthew reviews. Claude will give you a link.

### 4. Wait for Matthew's review
Matthew will either:
- **Approve** ✅ — he merges it to staging. You're done.
- **Request changes** 💬 — he leaves comments. Go to step 5.

**Do not merge it yourself, and never open a PR against `main`.** Only Matthew merges to staging, and only Matthew promotes staging to production.

### 5. If he requested changes
Tell Claude:
> "Address the PR review comments"

Claude reads Matthew's comments, makes the fixes, and updates the same pull request. Back to step 4.

---

## After it's merged
Tell Claude:
> "Delete my branch, I'm done with this one"

Then start over at step 1 for your next feature.

## If you get stuck
Just ask Claude what's going on — e.g. *"what branch am I on?"* or *"did my PR get created?"* — or message Matthew.
