#!/usr/bin/env bash
# SessionStart hook: when a contributor opens a session on a feature branch,
# fetch staging_environment and tell Claude whether the branch is behind, so
# Claude can bring it up to date (and resolve conflicts in plain language)
# before any work.

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Only relevant on feature branches — never auto-sync the protected branches
# themselves (production 'main', staging 'staging_environment', which are
# guarded by guard-branch.sh).
if [ -z "$branch" ] || [ "$branch" = "main" ] || [ "$branch" = "master" ] || [ "$branch" = "staging_environment" ]; then
  exit 0
fi

# Fetch quietly. If offline / auth fails, don't block the session.
git fetch origin staging_environment --quiet 2>/dev/null || exit 0

behind=$(git rev-list --count HEAD..origin/staging_environment 2>/dev/null)

if [ -z "$behind" ] || [ "$behind" = "0" ]; then
  msg="Contributor is on feature branch '$branch', already up to date with origin/staging_environment (the staging branch). No sync needed."
else
  msg="Contributor is on feature branch '$branch', which is $behind commit(s) BEHIND origin/staging_environment (the staging branch). Before doing other work, proactively bring it up to date: if there are uncommitted changes, commit or stash them first, then run 'git merge origin/staging_environment'. If it merges cleanly, briefly tell them their branch is now up to date. If there are merge conflicts, resolve them carefully, then explain clearly what you reconciled and confirm anything ambiguous with them before continuing."
fi

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"$msg"}}
EOF

exit 0
