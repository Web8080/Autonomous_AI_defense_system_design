#!/usr/bin/env bash
# Verify API gateway and dependency health. Exit 0 if all ok.
set -e
API="${API_URL:-http://localhost:8000}"
echo "Checking $API/health"
curl -sf "$API/health" | grep -q '"status":"ok"'
echo "API gateway OK"
