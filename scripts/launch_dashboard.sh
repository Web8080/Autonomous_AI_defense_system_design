#!/usr/bin/env bash
# One-shot: install deps, ensure replay is available, start dashboard. Run from repo root.
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "Ensuring replay is available for Simulation..."
mkdir -p dashboard/public/replay
if [ -f simulation/replay/railway_line_replay.json ]; then
  cp -f simulation/replay/railway_line_replay.json dashboard/public/replay/
  echo "Copied railway_line_replay.json to dashboard/public/replay/"
fi

echo "Installing dashboard dependencies..."
cd dashboard
npm install

echo "Starting dashboard at http://localhost:3000 ..."
echo "Log in with any email/password, then open Simulation to watch the replay."
exec npm run dev
