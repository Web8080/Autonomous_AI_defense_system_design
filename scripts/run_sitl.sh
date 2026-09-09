#!/usr/bin/env bash
# Start the PX4 SITL stack (Gazebo + drone bridge + mission service).
#
# First run builds PX4 from source (slow, ~10-20 min). Later runs are fast.
#
# The drone bridge needs at least one aircraft. By default this script registers
# a placeholder UUID so the whole dispatch path can be exercised. To match a real
# registered asset, export DRONE_ASSETS yourself before running.
set -euo pipefail
cd "$(dirname "$0")/.."

DEFAULT_ASSET="00000000-0000-0000-0000-000000000001"
export DRONE_ASSETS="${DRONE_ASSETS:-{\"$DEFAULT_ASSET\": {\"address\": \"udp://px4-sitl:14540\"}}}"

echo "Starting PX4 SITL stack with DRONE_ASSETS=$DRONE_ASSETS"
echo "First build takes a while; subsequent runs are incremental. Ctrl+C to stop."

exec docker compose -f docker-compose.sitl.yml up --build