#!/bin/bash
# Starts the planner's server when you log in, and runs the apartment finder every morning when the Mac wakes at 06:25.
# The job is set for 06:24, a minute before the wake (sudo pmset repeat wakeorpoweron MTWRFSU 06:25:00): that time
# passes while the Mac sleeps, so launchd starts the missed run the moment it wakes. A Mac with its lid closed stays
# awake only about 20 seconds after the wake, too short to wait for a later minute. The run is wrapped in caffeinate,
# so the Mac stays awake until it's done. It also starts at login and every hour with --daily, which does nothing once
# today's run has finished: a morning the Mac was off, restarted or asleep is caught up as soon as it's on again.
# Remove both again with: scripts/install-mac.sh --remove
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
AGENTS="$HOME/Library/LaunchAgents"
SERVER="se.bostadsplan.server"
SCRAPE="se.bostadsplan.scrape"

for label in "$SERVER" "$SCRAPE"; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
done
if [ "${1:-}" = "--remove" ]; then echo "Removed."; exit 0; fi

[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first."; exit 1; }
mkdir -p "$AGENTS" data

cat > "$AGENTS/$SERVER.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SERVER</string>
  <key>ProgramArguments</key><array><string>$ROOT/.venv/bin/python</string><string>$ROOT/server.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/server.out</string>
  <key>StandardErrorPath</key><string>$ROOT/data/server.out</string>
</dict></plist>
PLIST

cat > "$AGENTS/$SCRAPE.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SCRAPE</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-is</string><string>$ROOT/.venv/bin/python</string><string>$ROOT/scraper/finder.py</string><string>--daily</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>24</integer></dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/scraper.out</string>
  <key>StandardErrorPath</key><string>$ROOT/data/scraper.out</string>
</dict></plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$AGENTS/$SERVER.plist"
launchctl bootstrap "gui/$(id -u)" "$AGENTS/$SCRAPE.plist"
echo "Installed. The planner is at http://localhost:8765 and the finder runs every morning when the Mac wakes at 06:25, or as soon as it is on again if it missed that."
