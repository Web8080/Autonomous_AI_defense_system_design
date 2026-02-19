#!/usr/bin/env bash
# Run all services locally via Docker Compose. Load .env from repo root.
set -e
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
docker compose up -d postgres redis zookeeper kafka
echo "Waiting for Kafka..."
sleep 15
docker compose up -d asset-service telemetry-service alert-service control-service inference-service api-gateway
echo "Backend up. Dashboard: cd dashboard && npm run dev"
