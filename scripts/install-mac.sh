#!/bin/bash
# Starts the planner's server when you log in, and runs the apartment finder every day at 06:26.
# 06:26 falls inside the two minutes a Mac with its lid closed stays awake after the 06:25 wake; if it is asleep
# anyway, launchd runs the scraper when it next wakes. The run is wrapped in caffeinate,
# so a Mac woken for it (sudo pmset repeat wakeorpoweron MTWRFSU 06:25:00) stays awake until it's done.
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
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-is</string><string>$ROOT/.venv/bin/python</string><string>$ROOT/scraper/finder.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>26</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/data/scraper.out</string>
  <key>StandardErrorPath</key><string>$ROOT/data/scraper.out</string>
</dict></plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$AGENTS/$SERVER.plist"
launchctl bootstrap "gui/$(id -u)" "$AGENTS/$SCRAPE.plist"
echo "Installed. The planner is at http://localhost:8765 and the finder runs every day at 06:26."
