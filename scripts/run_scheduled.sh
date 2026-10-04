#!/bin/zsh
# One scheduled scrape, run by launchd every 6 hours (see
# scripts/install_mac_schedule.sh). Reads secrets from the gitignored files
# x_cookies.txt, sa.json and sheet_id.txt, appends to logs/scrape.log, and
# keeps each run's raw tweets in runs/ (for --prune-sheet), for 60 days.
set -uo pipefail
cd "${0:A:h}/.."
mkdir -p logs runs
exec >>logs/scrape.log 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') start"
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
  .venv/bin/python -m cfb_offers --max-pages 5 --dump-raw "runs/$(date +%Y%m%d-%H%M).jsonl" &
pid=$!
# launchd skips every later run while this one is alive, so a hung scrape
# would silently stop the schedule. Kill it after 45 minutes (macOS has no
# `timeout`).
( sleep 2700 && kill $pid 2>/dev/null && echo "=== killed after 45 min" ) &
watchdog=$!
wait $pid
code=$?
pkill -P $watchdog 2>/dev/null; kill $watchdog 2>/dev/null

if (( code == 0 )); then
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ok"
else
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') FAILED (exit $code)"
  osascript -e 'display notification "Scrape failed - see logs/scrape.log" with title "CFB offer tracker"'
fi
find runs -name '*.jsonl' -mtime +60 -delete
exit $code
