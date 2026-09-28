#!/bin/zsh
# Installs (or reinstalls) the launchd job that runs scripts/run_scheduled.sh
# at 00:17, 06:17, 12:17 and 18:17. Runs missed while the Mac was asleep run
# once when it wakes. Uninstall:
#   launchctl bootout gui/$(id -u)/com.cfboffers.scrape
#   rm ~/Library/LaunchAgents/com.cfboffers.scrape.plist
set -euo pipefail
repo="${0:A:h:h}"
label="com.cfboffers.scrape"
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$repo/logs"
chmod +x "$repo/scripts/run_scheduled.sh"

intervals=""
for h in 0 6 12 18; do
  intervals+="<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>17</integer></dict>"
done

cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$repo/scripts/run_scheduled.sh</string></array>
  <key>StartCalendarInterval</key><array>$intervals</array>
  <key>StandardOutPath</key><string>$repo/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$repo/logs/launchd.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "installed $label -> $plist"
echo "run once now:   launchctl kickstart gui/\$(id -u)/$label"
echo "watch the log:  tail -f \"$repo/logs/scrape.log\""
