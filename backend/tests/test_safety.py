"""Unit tests for the drone bridge safety layer (pure logic, no MAVSDK)."""
import sys
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "shared"))
sys.path.insert(0, str(BACKEND / "services"))

from drone_bridge.safety import (  # noqa: E402
    SafetyEnvelope,
    SafetyViolation,
    Severity,
    check_pre_arm,
    check_mission_plan,
    check_in_flight,
    within_geofence,
    point_in_polygon,
    haversine_m,
    worst_severity,
)


class FakeState:
    """Minimal state object exposing the fields safety.py reads."""

    def __init__(self, **kwargs):
        self.asset_id = kwargs.pop("asset_id", "fake")
        self.latitude = kwargs.pop("latitude", 51.5)
        self.longitude = kwargs.pop("longitude", -0.12)
        self.altitude_rel_m = kwargs.pop("altitude_rel_m", 10.0)
        self.altitude_amsl_m = kwargs.pop("altitude_amsl_m", 100.0)
        self.battery_pct = kwargs.pop("battery_pct", 80.0)
        self.gps_fix_type = kwargs.pop("gps_fix_type", 3)
        self.gps_satellites = kwargs.pop("gps_satellites", 12)
        self.flight_mode = kwargs.pop("flight_mode", "AUTO.MISSION")
        self.armed = kwargs.pop("armed", True)
        self.in_air = kwargs.pop("in_air", False)
        self.health_all_ok = kwargs.pop("health_all_ok", True)
        self.groundspeed_ms = kwargs.pop("groundspeed_ms", 5.0)
        self.seconds_since_heartbeat = kwargs.pop("seconds_since_heartbeat", 0.1)
        self.last_heartbeat = 0.0

    @property
    def has_position(self):
        return self.latitude is not None and self.longitude is not None


def default_env():
    return SafetyEnvelope(
        max_altitude_m=120.0,
        min_battery_arm_pct=40.0,
        rtl_battery_pct=30.0,
        land_battery_pct=15.0,
        max_speed_ms=12.0,
        max_distance_m=2000.0,
        inclusion_zones=None,
        exclusion_zones=None,
        home_lat=51.5,
        home_lon=-0.12,
    )


def codes(violations):
    return sorted(v.code for v in violations)


def test_haversine_m_sanity():
    # ~1 degree of latitude along a meridian ≈ 111 km
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < d < 112_000


def test_point_in_polygon_square():
    sq = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    assert point_in_polygon(0.5, 0.5, sq)
    assert not point_in_polygon(2.0, 2.0, sq)


def test_pre_arm_passes_for_healthy_approved():
    state = FakeState()
    env = default_env()
    assert check_pre_arm(state, env, approved=True) == []


def test_pre_arm_blocks_unapproved():
    state = FakeState()
    env = default_env()
    violations = check_pre_arm(state, env, approved=False)
    assert "not_approved" in codes(violations)
    assert all(v.severity == Severity.BLOCK for v in violations)


def test_pre_arm_blocks_low_battery():
    state = FakeState(battery_pct=20.0)
    env = default_env()
    assert "battery_low" in codes(check_pre_arm(state, env, approved=True))


def test_pre_arm_blocks_bad_gps():
    state = FakeState(gps_fix_type=1, gps_satellites=3)
    env = default_env()
    assert {"gps_fix", "gps_satellites"} <= set(codes(check_pre_arm(state, env, approved=True)))


def test_pre_arm_blocks_no_position():
    state = FakeState(latitude=None, longitude=None)
    env = default_env()
    assert "no_position" in codes(check_pre_arm(state, env, approved=True))


def test_pre_arm_blocks_stale_link():
    state = FakeState(seconds_since_heartbeat=10.0)
    env = default_env()
    assert "link_stale" in codes(check_pre_arm(state, env, approved=True))


def test_pre_arm_geofence_exclusion():
    square = [(51.49, -0.13), (51.49, -0.11), (51.51, -0.11), (51.51, -0.13)]
    env = default_env()
    env.exclusion_zones = [square]
    state = FakeState(latitude=51.5, longitude=-0.12)  # inside the square
    violations = check_pre_arm(state, env, approved=True)
    assert "geofence_exclusion" in codes(violations)


def test_pre_arm_inclusion_permissive_by_absence():
    state = FakeState()
    env = default_env()
    env.inclusion_zones = None
    assert check_pre_arm(state, env, approved=True) == []


def test_pre_arm_inclusion_requires_inside():
    env = default_env()
    env.inclusion_zones = [[(10.0, 10.0), (10.0, 11.0), (11.0, 11.0), (11.0, 10.0)]]
    state = FakeState(latitude=51.5, longitude=-0.12)
    assert "geofence_inclusion" in codes(check_pre_arm(state, env, approved=True))


def test_mission_plan_blocks_bad_altitude():
    env = default_env()
    wps = [
        {"latitude": 51.5, "longitude": -0.12, "altitude_m": 130.0, "speed_ms": 5.0},
        {"latitude": 51.51, "longitude": -0.13, "altitude_m": 60.0, "speed_ms": 5.0},
    ]
    violations = check_mission_plan(wps, env)
    assert "waypoint_altitude" in codes(violations)


def test_mission_plan_blocks_empty():
    env = default_env()
    assert "empty_mission" in codes(check_mission_plan([], env))


def test_mission_plan_blocks_out_of_range_coords():
    env = default_env()
    wps = [{"latitude": 91.0, "longitude": 0.0, "altitude_m": 60.0}]
    assert "waypoint_bad_coords" in codes(check_mission_plan(wps, env))


def test_mission_plan_blocks_distance():
    env = default_env()
    wps = [{"latitude": 51.5, "longitude": 0.5, "altitude_m": 60.0}]
    violations = check_mission_plan(wps, env)
    assert "waypoint_distance" in codes(violations)


def test_in_flight_aborts_on_battery_critical():
    env = default_env()
    state = FakeState(battery_pct=10.0)
    violations = check_in_flight(state, env)
    assert "battery_critical" in codes(violations)
    assert any(v.severity == Severity.ABORT for v in violations)


def test_in_flight_aborts_on_altitude_ceiling():
    env = default_env()
    state = FakeState(altitude_rel_m=200.0)
    violations = check_in_flight(state, env)
    assert "altitude_exceeded" in codes(violations)


def test_in_flight_aborts_on_link_loss():
    env = default_env()
    state = FakeState(seconds_since_heartbeat=10.0)
    violations = check_in_flight(state, env)
    assert "link_lost" in codes(violations)


def test_in_flight_warns_on_overspeed_only():
    env = default_env()
    state = FakeState(groundspeed_ms=30.0)
    violations = check_in_flight(state, env)
    assert [v.code for v in violations] == ["overspeed"]
    assert violations[0].severity == Severity.WARN


def test_in_flight_distance_from_home():
    env = default_env()
    # ~50km east of home; way outside the 2km limit
    state = FakeState(latitude=51.5, longitude=0.3)
    violations = check_in_flight(state, env)
    assert "distance_exceeded" in codes(violations)


def test_worst_severity_precedence():
    w = SafetyViolation("w", "w", Severity.WARN)
    a = SafetyViolation("a", "a", Severity.ABORT)
    b = SafetyViolation("b", "b", Severity.BLOCK)
    assert worst_severity([w]) == Severity.WARN
    assert worst_severity([w, a]) == Severity.ABORT
    assert worst_severity([w, a, b]) == Severity.BLOCK
    assert worst_severity([]) is None