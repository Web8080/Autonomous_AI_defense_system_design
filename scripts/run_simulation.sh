#!/usr/bin/env bash
# Start simulation: agent + sensor emulator. Requires Kafka (run_local.sh first).
# Keeps running until Ctrl+C; then stops both simulators.
set -e
cd "$(dirname "$0")/.."
export KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
cd simulation
pip install -q -r requirements.txt
python agent_simulator.py &
PID1=$!
python sensor_emulator.py &
PID2=$!
echo "Simulation running (agent PID $PID1, sensor PID $PID2). Ctrl+C to stop."
trap "kill $PID1 $PID2 2>/dev/null; exit 0" INT TERM
wait
