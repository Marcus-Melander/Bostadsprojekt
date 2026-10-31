#!/bin/bash
# One-time setup: a Python environment with Playwright and its Chromium.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
# The scraper drives your installed Google Chrome. Without it, fetch Playwright's Chromium.
[ -d "/Applications/Google Chrome.app" ] || .venv/bin/python -m playwright install chromium
mkdir -p data
echo "Ready. Start the planner with: .venv/bin/python server.py"
