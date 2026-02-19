# Testing the Simulation

How to get services up and run the agent and sensor simulators so you can test the pipeline end to end.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for running the simulation scripts on your host, or use the optional simulation container)
- (Optional) Node 20 for the dashboard

## Option A: Full stack + simulation (recommended)

### 1. Start infrastructure and backend

From the repo root:

```bash
# Load env if you have one (optional)
[ -f .env ] && set -a && source .env && set +a

# Start Postgres, Redis, Kafka, then all backend services
docker compose up -d postgres redis zookeeper kafka
# Wait for Kafka to be ready
sleep 15
docker compose up -d asset-service telemetry-service alert-service control-service inference-service api-gateway
```

Or use the helper script:

```bash
./scripts/run_local.sh
```

Wait until all containers are up (`docker compose ps`). Gateway is at http://localhost:8000.

### 2. Create an asset (so telemetry has a valid asset_id)

The simulator uses a random UUID by default. Either create an asset via the API with that ID, or use a fixed ID for the sim:

```bash
# Optional: create asset with a known ID for the simulator
curl -s -X POST http://localhost:8000/api/v1/assets \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dev-token" \
  -d '{"name":"Sim Drone","asset_type":"drone","region_id":"region-1"}' | jq
```

Note: the gateway requires a valid JWT. With `ALLOW_DEV_TOKEN=true` (set in docker-compose for local), use `Authorization: Bearer dev-token`. If you use a real JWT, get one with `JWT_SECRET=your_32_char_secret python scripts/gen_jwt_dev.py`.

### 3. Run the simulation

From the repo root, with Kafka reachable at `localhost:9092`:

```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
cd simulation
pip install -r requirements.txt
# Terminal 1: agent (telemetry.raw)
python agent_simulator.py
# Terminal 2: sensor (inference.frames)
python sensor_emulator.py
```

Or run both in the background and leave them running:

```bash
./scripts/run_simulation.sh
```

Keep that terminal open (or run in tmux/screen). Stop with Ctrl+C.

### 4. Verify data flow

- **Kafka**: List topics and consume a few messages (from host with Kafka on 9092):

  ```bash
  docker compose exec kafka kafka-topics --bootstrap-server localhost:9092 --list
  docker compose exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic telemetry.raw --from-beginning --max-messages 3
  ```

- **Telemetry in DB**: The telemetry service has a Kafka consumer that writes to `telemetry.aggregated`. Run it manually so ingested telemetry is stored:

  ```bash
  cd backend && PYTHONPATH=shared KAFKA_BOOTSTRAP_SERVERS=localhost:9092 DATABASE_URL=postgresql://defense:defense@localhost:5432/defense python services/telemetry_service/kafka_consumer.py
  ```

  Then query: `GET http://localhost:8000/api/v1/telemetry/aggregated` with `Authorization: Bearer dev-token`.

- **Dashboard**: From repo root, `cd dashboard && npm install && npm run dev`. Open http://localhost:3000, log in (uses dev-token when API is localhost). Map and assets will show data once the consumer has run and assets exist.

## Option B: Minimal (Kafka + simulation only)

To test only that the simulators publish to Kafka (no backend or DB):

```bash
docker compose up -d zookeeper kafka
sleep 15
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
cd simulation && pip install -r requirements.txt
python agent_simulator.py &
python sensor_emulator.py &
# Verify
docker compose exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic telemetry.raw --from-beginning --max-messages 5
```

## Environment variables (simulation)

| Variable | Default | Description |
|----------|---------|-------------|
| KAFKA_BOOTSTRAP_SERVERS | localhost:9092 | Kafka brokers (use host:9092 when Kafka runs in Docker) |
| TELEMETRY_RAW_TOPIC | telemetry.raw | Topic for agent telemetry |
| INFERENCE_FRAMES_TOPIC | inference.frames | Topic for sensor frames |
| SIM_ASSET_ID | random UUID | Asset ID in messages (set to match an asset in DB if you want aggregation) |
| SIM_REGION_ID | region-1 | Region in payload |
| SIM_INTERVAL_SEC | 1.0 | Agent publish interval (seconds) |
| SIM_FRAME_INTERVAL_SEC | 2.0 | Sensor frame publish interval (seconds) |

## Troubleshooting

- **Connection refused to Kafka**: Ensure Kafka is up and exposed on 9092 (`docker compose ps`). From the host use `localhost:9092`; from another container use `kafka:29092`.
- **401 from API**: Set `ALLOW_DEV_TOKEN=true` on the api-gateway and use `Authorization: Bearer dev-token`, or issue a real JWT with `scripts/gen_jwt_dev.py`.
- **No telemetry in API**: Run the telemetry Kafka consumer (see step 4 above); the main telemetry service only serves and ingests via HTTP by default.
- **Simulation script exits**: Run `./scripts/run_simulation.sh` and leave the terminal open, or run `agent_simulator.py` and `sensor_emulator.py` in two separate terminals.
