#!/bin/zsh
# One scheduled scrape, run by launchd every 6 hours (see
# scripts/install_mac_schedule.sh). Usage: run_scheduled.sh offers|visits -
# offers and visits run as separate jobs, 3 hours apart, so each run's X
# searches stay small. Reads secrets from the gitignored files
# x_cookies.txt, sa.json and sheet_id.txt, appends to logs/scrape.log, and
# keeps each run's raw tweets in runs/ (for --prune-sheet), for 60 days.
set -uo pipefail
cd "${0:A:h}/.."
mkdir -p logs runs
exec >>logs/scrape.log 2>&1

mode="${1:-offers}"
if [[ $mode != offers && $mode != visits ]]; then
  echo "=== FAILED: unknown mode '$mode' (expected offers or visits)"
  exit 2
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') start $mode"
for f in x_cookies.txt sa.json sheet_id.txt; do
  if [[ ! -s $f ]]; then
    echo "=== FAILED: missing $f"
    osascript -e "display notification \"Missing $f\" with title \"CFB offer tracker\""
    exit 1
  fi
done

X_COOKIES="$(cat x_cookies.txt)" \
GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" \
SHEET_ID="$(tr -d '[:space:]' < sheet_id.txt)" \
  .venv/bin/python -m cfb_offers --$mode-only --max-pages 5 \
    --dump-raw "runs/$(date +%Y%m%d-%H%M)-$mode.jsonl" &
pid=$!
# launchd skips every later run while this one is alive, so a hung scrape
# would silently stop the schedule. Kill it after 60 minutes (macOS has no
# `timeout`).
( sleep 3600 && kill $pid 2>/dev/null && echo "=== killed after 60 min" ) &
watchdog=$!
wait $pid
code=$?
pkill -P $watchdog 2>/dev/null; kill $watchdog 2>/dev/null

if (( code == 0 )); then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ok $mode"
else
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') FAILED $mode (exit $code)"
  osascript -e "display notification \"$mode scrape failed - see logs/scrape.log\" with title \"CFB offer tracker\""
fi
find runs -name '*.jsonl' -mtime +60 -delete
exit $code
