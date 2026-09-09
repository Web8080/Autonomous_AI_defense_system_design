"""Unit tests for MissionExecutor using a mocked MAVSDK System."""
import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "shared"))
sys.path.insert(0, str(BACKEND / "services"))

from drone_bridge.connection import VehicleLink, LinkState  # noqa: E402
from drone_bridge.mission import MissionExecutor, MissionPlan, MissionStatus  # noqa: E402
from drone_bridge.safety import SafetyEnvelope  # noqa: E402


class FakeAction:
    def __init__(self, log):
        self.log = log

    async def arm(self):
        self.log.append("arm")

    async def disarm(self):
        self.log.append("disarm")

    async def return_to_launch(self):
        self.log.append("rtl")

    async def kill(self):
        self.log.append("kill")


class FakeTelemetry:
    def __init__(self):
        self.armed_holder = {"armed": False}
        self.health_holder = {"is_ok": True}

    async def armed(self):
        # Yield current value, then flip to True so the executor proceeds.
        while True:
            yield self.armed_holder["armed"]
            self.armed_holder["armed"] = True

    async def health(self):
        while True:
            yield FakeHealth(self.health_holder["is_ok"])

    async def position(self):
        while True:
            yield FakePosition()


class FakeHealth:
    def __init__(self, is_ok):
        self.is_ok = is_ok


class FakePosition:
    latitude_deg = 51.5
    longitude_deg = -0.12
    relative_altitude_m = 10.0
    absolute_altitude_m = 100.0


class FakeMissionProgress:
    current = 1
    total = 1


class FakeMission:
    def __init__(self, log):
        self.log = log
        self.start_called = False

    async def set_return_to_launch_after_mission(self, v):
        self.log.append(f"set_rtl_after={v}")

    async def upload_mission(self, plan):
        self.log.append("upload")

    async def mission_progress(self):
        while True:
            yield FakeMissionProgress()
            self.start_called = True

    async def start_mission(self):
        self.start_called = True
        self.log.append("start")


class FakeParam:
    def __init__(self, log):
        self.log = log

    async def set_param_int(self, name, value):
        self.log.append(f"param_int={name}={value}")

    async def set_param_float(self, name, value):
        self.log.append(f"param_float={name}={value}")


class FakeCore:
    class ConnectionState:
        is_connected = True

    async def connection_state(self):
        while True:
            yield self.ConnectionState()


class FakeSystem:
    def __init__(self):
        self.log = []
        self.action = FakeAction(self.log)
        self.telemetry = FakeTelemetry()
        self.mission = FakeMission(self.log)
        self.param = FakeParam(self.log)
        self.core = FakeCore()


# ---------------------------------------------------------------------------
# Fake MAVSDK, injected into sys.modules so the executor's lazy imports
# resolve without installing the native mavsdk wheel in CI.
# ---------------------------------------------------------------------------

class FakeMissionItem:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeMissionPlanWrapper:
    def __init__(self, mission_items=None):
        self.mission_items = mission_items or []


def _install_fake_mavsdk():
    import sys

    fake_mission = sys.modules.setdefault("mavsdk.mission", type(sys)("mavsdk.mission"))
    fake_mission.MissionItem = FakeMissionItem
    fake_mission.MissionPlan = FakeMissionPlanWrapper
    fake_mission.MissionStatus = type("MissionStatus", (), {})

    fake_mavsdk = sys.modules.setdefault("mavsdk", type(sys)("mavsdk"))
    fake_mavsdk.mission = fake_mission
    fake_mavsdk.System = FakeSystem


_install_fake_mavsdk()


def make_link(system):
    link = VehicleLink(asset_id="a", system_address="udp://localhost:14540")
    link.system = system
    link.link_state = LinkState.CONNECTED
    return link


def default_env():
    return SafetyEnvelope(
        max_altitude_m=120.0,
        min_battery_arm_pct=40.0,
        rtl_battery_pct=30.0,
        land_battery_pct=15.0,
        max_speed_ms=12.0,
        max_distance_m=2000.0,
        home_lat=51.5,
        home_lon=-0.12,
    )


def waypoints():
    return [
        {"latitude": 51.5, "longitude": -0.12, "altitude_m": 40.0, "speed_ms": 5.0},
        {"latitude": 51.51, "longitude": -0.13, "altitude_m": 40.0, "speed_ms": 5.0},
    ]


@pytest.mark.asyncio
async def test_executor_rejects_unapproved():
    system = FakeSystem()
    link = make_link(system)
    plan = MissionPlan(
        mission_id=None, asset_id="a", flight_id=None,
        waypoints=waypoints(), envelope=default_env(), approved=False,
    )
    ex = MissionExecutor(link, plan)
    status = await ex.run()
    assert status == MissionStatus.FAILED
    assert not ex.mission.approved


@pytest.mark.asyncio
async def test_executor_rejects_bad_waypoints():
    system = FakeSystem()
    link = make_link(system)
    plan = MissionPlan(
        mission_id=None, asset_id="a", flight_id=None,
        waypoints=[{"latitude": 51.5, "longitude": -0.12, "altitude_m": 400.0}],
        envelope=default_env(), approved=True,
    )
    ex = MissionExecutor(link, plan)
    status = await ex.run()
    assert status == MissionStatus.FAILED


@pytest.mark.asyncio
async def test_executor_runs_and_completes():
    system = FakeSystem()
    link = make_link(system)
    plan = MissionPlan(
        mission_id=None, asset_id="a", flight_id="f",
        waypoints=waypoints(), envelope=default_env(), approved=True,
    )
    ex = MissionExecutor(link, plan)
    status = await ex.run()
    assert status == MissionStatus.COMPLETED
    assert "arm" in system.log
    assert "upload" in system.log
    assert "start" in system.log
    assert "set_rtl_after=True" in system.log
    assert any(l.startswith("param_float=") for l in system.log)  # FC params pushed


@pytest.mark.asyncio
async def test_executor_cancel_triggers_rtl():
    system = FakeSystem()
    link = make_link(system)
    plan = MissionPlan(
        mission_id=None, asset_id="a", flight_id="f",
        waypoints=waypoints(), envelope=default_env(), approved=True,
    )
    ex = MissionExecutor(link, plan)
    ex._stop.set()  # simulate cancel before run even starts
    await ex.cancel()
    assert ex.status == MissionStatus.ABORTED
    assert "rtl" in system.log