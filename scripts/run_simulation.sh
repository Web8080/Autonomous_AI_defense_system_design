#!/usr/bin/env bash
# Start simulation: agent + sensor emulator. Requires Kafka (run_local.sh first).
set -e
cd "$(dirname "$0")/.."
export KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
cd simulation
pip install -q -r requirements.txt
python agent_simulator.py &
python sensor_emulator.py &
echo "Simulation running. Stop with Ctrl+C."
