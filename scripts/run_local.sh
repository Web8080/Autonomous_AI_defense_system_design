#!/usr/bin/env bash
# Run the full local stack via Docker Compose. Load .env from repo root.
set -e
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
docker compose up -d postgres redis redpanda
echo "Waiting for dependencies..."
sleep 15
docker compose up -d \
  asset-service telemetry-service telemetry-consumer alert-service control-service \
  mission-service inference-service ml-service detections-consumer \
  auth-service api-gateway drone-bridge simulation-service
echo "Backend up. Dashboard: cd dashboard && npm run dev"

echo "Sample commands:"
echo "  curl http://localhost:8000/api/v1/assets"
echo "  docker compose ps"