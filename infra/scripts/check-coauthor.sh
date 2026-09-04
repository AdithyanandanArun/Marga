#!/bin/bash
# check-coauthor.sh — Fail CI if any new commits contain Co-authored-by trailers.
# Usage: BASE_SHA=<merge-base> ./check-coauthor.sh
set -euo pipefail

BASE_SHA="${BASE_SHA:-origin/main}"

if git log "${BASE_SHA}..HEAD" --format=%B | grep -qi '^Co-authored-by:'; then
  echo "ERROR: Co-authored-by trailers are forbidden in this repository."
  echo "Please amend or rebase to remove them before merging."
  exit 1
fi

echo "OK: No forbidden Co-authored-by trailers found."
