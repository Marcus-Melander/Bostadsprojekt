#!/bin/bash
# Starts the local planner's server when you log in. The finder no longer runs on the Mac: the morning run happens on
# GitHub's servers (.github/workflows/morning.yml) and the planner is online. Running this again also removes the
# finder's old hourly job (se.bostadsplan.scrape). To fetch the cloud's latest state for the local planner:
# .venv/bin/python scraper/state.py --pull
# Remove everything with: scripts/install-mac.sh --remove
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


launchctl bootstrap "gui/$(id -u)" "$AGENTS/$SERVER.plist"
echo "Installed. The planner is at http://localhost:8765. The finder runs every morning on GitHub's servers, not on this Mac."
