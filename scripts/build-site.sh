#!/bin/sh
# What Vercel serves (vercel.json): the page, its online part and the area map, nothing else from the repo.
# The project's address and publishable key come from Vercel's environment variables SUPABASE_URL and
# SUPABASE_ANON_KEY; they are public by design, and row-level security decides what they open.
set -e
cd "$(dirname "$0")/.."
rm -rf site && mkdir site
cp index.html online.js areas.geojson site/
printf 'window.BOSTADSPLAN = { url: "%s", anonKey: "%s" };\n' "${SUPABASE_URL:-}" "${SUPABASE_ANON_KEY:-}" > site/config.js
echo "Built site/ for ${SUPABASE_URL:-no project yet}"
