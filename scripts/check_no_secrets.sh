#!/usr/bin/env bash
# Pre-push safety check: refuses to let obviously-sensitive files get
# staged for commit. Run this before every `git push` (or wire it up as a
# pre-commit/pre-push hook).
#
# Usage: scripts/check_no_secrets.sh
# Exit status: 0 if nothing sensitive would be staged, 1 otherwise.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# `git add -A --dry-run` lists everything that *would* be staged (new,
# modified, deleted) without actually staging anything, as lines like:
#   add 'path/to/file'
#   remove 'path/to/other'
staged_output="$(git add -A --dry-run 2>/dev/null || true)"

if [ -z "${staged_output}" ]; then
  echo "check_no_secrets: nothing to stage, nothing to check."
  exit 0
fi

# Extract just the quoted paths.
paths="$(echo "${staged_output}" | sed -n "s/^[a-z]* '\(.*\)'$/\1/p")"

if [ -z "${paths}" ]; then
  echo "check_no_secrets: could not parse 'git add -A --dry-run' output:"
  echo "${staged_output}"
  exit 1
fi

# Case-insensitive patterns for files that must never be committed to this
# public repo: X cookie exports, the Google service-account key, twscrape's
# accounts DB, any CSV/JSONL scrape output, and .env files.
declare -a patterns=(
  '(^|/)x_cookies\.txt$'
  '(^|/)cookies\.json$'
  'cookie'
  '(^|/)sa\.json$'
  '(^|/)service-account\.json$'
  'service.?account.*\.json$'
  '\.csv$'
  '\.jsonl$'
  '(^|/)accounts\.db$'
  '(^|/)\.env$'
  '\.env$'
)

found=0
while IFS= read -r path; do
  [ -z "${path}" ] && continue
  lower="$(echo "${path}" | tr '[:upper:]' '[:lower:]')"
  for pattern in "${patterns[@]}"; do
    if echo "${lower}" | grep -Eq "${pattern}"; then
      echo "check_no_secrets: REFUSING - '${path}' matches sensitive pattern '${pattern}'"
      found=1
      break
    fi
  done
done <<< "${paths}"

if [ "${found}" -ne 0 ]; then
  echo
  echo "One or more sensitive files would be staged. Remove them (and make"
  echo "sure they're covered by .gitignore) before committing/pushing to"
  echo "this public repo."
  exit 1
fi

echo "check_no_secrets: OK - no sensitive files would be staged."
exit 0
