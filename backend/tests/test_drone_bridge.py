"""Unit tests for drone_bridge connection + bridge registry (no MAVSDK needed)."""
import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "shared"))
sys.path.insert(0, str(BACKEND / "services"))

from drone_bridge.connection import VehicleLink, LinkRegistry, LinkState, VehicleState  # noqa: E402
from drone_bridge.mavlink_bridge import BridgeRegistry  # noqa: E402


def test_vehicle_state_has_position():
    s = VehicleState(asset_id="a")
    assert not s.has_position
    s.latitude, s.longitude = 1.0, 1.0
    assert s.has_position


def test_vehicle_state_to_dict_round_trip():
    s = VehicleState(asset_id="x", org_id="o", latitude=51.5, longitude=-0.1, battery_pct=60.0)
    d = s.to_dict()
    assert d["asset_id"] == "x"
    assert d["org_id"] == "o"
    assert d["latitude"] == 51.5
    assert d["battery_pct"] == 60.0
    assert "seconds_since_heartbeat" in d


def test_vehicle_link_refresh_downgrades_on_stale_heartbeat():
    import time
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    link.link_state = LinkState.CONNECTED
    link.state.last_heartbeat = time.monotonic() - 10.0  # stale
    assert link.refresh_link_state() == LinkState.DEGRADED
    assert not link.is_usable


def test_vehicle_link_touch_heartbeat_recovers():
    import time
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    link.link_state = LinkState.CONNECTED
    link.state.last_heartbeat = time.monotonic() - 10.0
    link.refresh_link_state()  # now DEGRADED
    link.touch_heartbeat()
    assert link.state.seconds_since_heartbeat < 1.0
    assert link.link_state == LinkState.CONNECTED


@pytest.mark.asyncio
async def test_link_registry_add_get_remove():
    reg = LinkRegistry()
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    await reg.add(link)
    assert reg.get("a") is link
    assert len(reg.all()) == 1
    await reg.remove("a")
    assert reg.get("a") is None


@pytest.mark.asyncio
async def test_link_registry_replace_closes_old():
    reg = LinkRegistry()
    old = VehicleLink(asset_id="a", system_address="x")
    await reg.add(old)
    new = VehicleLink(asset_id="a", system_address="y")
    await reg.add(new)
    assert reg.get("a") is new
    assert old.link_state == LinkState.DISCONNECTED


@pytest.mark.asyncio
async def test_bridge_registry_requires_connected_link():
    reg = LinkRegistry()
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    link.link_state = LinkState.DISCONNECTED
    await reg.add(link)

    bridges = BridgeRegistry(reg)
    bridge = await bridges.create("a")
    assert bridge is None  # not connected -> no bridge


@pytest.mark.asyncio
async def test_bridge_registry_create_connected():
    reg = LinkRegistry()
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    link.link_state = LinkState.CONNECTED
    await reg.add(link)

    bridges = BridgeRegistry(reg)
    bridge = await bridges.create("a")
    assert bridge is not None
    assert bridges.get("a") is bridge
    await bridges.remove("a")
    assert bridges.get("a") is None