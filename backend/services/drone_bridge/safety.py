"""
Safety layer: pre-arm gates and in-flight guards.

Design principle, and the reason this file is structured the way it is: software
checks in this process are the SECOND layer, never the only one. Anything that
must hold when this service crashes, the network drops, or Python stalls is also
pushed to the flight controller as a PX4 parameter (see `apply_fc_safety_params`).
A check that exists only here is a check that does not exist during the failure
modes you actually care about.

Layers, outermost first:
  1. Pilot RC kill switch          - hardware, always live, not ours to override
  2. PX4 onboard failsafes         - params we set, enforced by the FC
  3. This module                   - pre-arm gates and in-flight monitoring
  4. Operator approval gate        - a named human before any autonomous dispatch
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

log = logging.getLogger("drone_bridge.safety")

# Defaults. Per-site values come from orgs.sites / missions.geofences and override these.
DEFAULT_MAX_ALTITUDE_M = 120.0        # UK/EU open-category ceiling
DEFAULT_MIN_BATTERY_ARM_PCT = 40.0
DEFAULT_RTL_BATTERY_PCT = 30.0
DEFAULT_LAND_BATTERY_PCT = 15.0
DEFAULT_MAX_SPEED_MS = 12.0
DEFAULT_MAX_DISTANCE_M = 2000.0
MIN_GPS_FIX_TYPE = 3                  # 3D fix
MIN_GPS_SATELLITES = 8
HEARTBEAT_TIMEOUT_S = 3.0

EARTH_RADIUS_M = 6_371_000.0


class Severity(str, Enum):
    BLOCK = "block"      # refuse the command outright
    ABORT = "abort"      # in flight: return to launch
    WARN = "warn"        # log and surface, continue


@dataclass(frozen=True)
class SafetyViolation:
    code: str
    message: str
    severity: Severity

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "severity": self.severity.value}


@dataclass
class SafetyEnvelope:
    """The operating limits for one flight, assembled from site and mission config."""
    max_altitude_m: float = DEFAULT_MAX_ALTITUDE_M
    min_battery_arm_pct: float = DEFAULT_MIN_BATTERY_ARM_PCT
    rtl_battery_pct: float = DEFAULT_RTL_BATTERY_PCT
    land_battery_pct: float = DEFAULT_LAND_BATTERY_PCT
    max_speed_ms: float = DEFAULT_MAX_SPEED_MS
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M
    # Inclusion polygons: the aircraft must stay inside at least one.
    # Exclusion polygons: it must never enter any.
    # Each polygon is a sequence of (lat, lon), assumed closed.
    inclusion_zones: list[Sequence[tuple[float, float]]] | None = None
    exclusion_zones: list[Sequence[tuple[float, float]]] | None = None
    home_lat: float | None = None
    home_lon: float | None = None

    def __post_init__(self) -> None:
        if self.inclusion_zones is None:
            self.inclusion_zones = []
        if self.exclusion_zones is None:
            self.exclusion_zones = []


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def point_in_polygon(lat: float, lon: float, polygon: Sequence[tuple[float, float]]) -> bool:
    """Ray casting. Polygon is [(lat, lon), ...].

    The repo's previous implementation lived in simulation/autonomous_agent.py,
    operated on a hardcoded 1000x1000 unit square, and was called as
    `in_geofence(0, 0)` regardless of where the aircraft actually was. This one
    takes the real position.
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        if (lon_i > lon) != (lon_j > lon):
            denom = lon_j - lon_i
            if denom != 0:
                lat_at_lon = lat_i + (lon - lon_i) / denom * (lat_j - lat_i)
                if lat < lat_at_lon:
                    inside = not inside
        j = i
    return inside


def within_geofence(lat: float, lon: float, env: SafetyEnvelope) -> SafetyViolation | None:
    """Inclusion zones are permissive-by-absence: no zones configured means no
    containment constraint. Exclusion zones always bind."""
    for zone in env.exclusion_zones or []:
        if point_in_polygon(lat, lon, zone):
            return SafetyViolation(
                "geofence_exclusion",
                "Position is inside an exclusion zone",
                Severity.ABORT,
            )
    inclusion = env.inclusion_zones or []
    if inclusion and not any(point_in_polygon(lat, lon, z) for z in inclusion):
        return SafetyViolation(
            "geofence_inclusion",
            "Position is outside every inclusion zone",
            Severity.ABORT,
        )
    return None


# ---------------------------------------------------------------------------
# Pre-arm gates
# ---------------------------------------------------------------------------

def check_pre_arm(state: Any, env: SafetyEnvelope, *, approved: bool) -> list[SafetyViolation]:
    """Every blocking reason the aircraft must not arm.

    Returns all violations rather than the first, so an operator sees the whole
    picture instead of fixing one problem at a time.
    """
    v: list[SafetyViolation] = []

    # The human oversight gate. Deliberately first: it is the one that is a
    # product commitment rather than an engineering limit.
    if not approved:
        v.append(SafetyViolation(
            "not_approved",
            "Flight has not been approved by an operator",
            Severity.BLOCK,
        ))

    if state.seconds_since_heartbeat > HEARTBEAT_TIMEOUT_S:
        v.append(SafetyViolation(
            "link_stale",
            f"No heartbeat for {state.seconds_since_heartbeat:.1f}s",
            Severity.BLOCK,
        ))

    if not state.health_all_ok:
        v.append(SafetyViolation(
            "vehicle_health",
            "Vehicle reports a failed health check",
            Severity.BLOCK,
        ))

    if state.gps_fix_type is None or state.gps_fix_type < MIN_GPS_FIX_TYPE:
        v.append(SafetyViolation(
            "gps_fix",
            f"GPS fix type {state.gps_fix_type} is below required {MIN_GPS_FIX_TYPE}",
            Severity.BLOCK,
        ))

    if state.gps_satellites is not None and state.gps_satellites < MIN_GPS_SATELLITES:
        v.append(SafetyViolation(
            "gps_satellites",
            f"Only {state.gps_satellites} satellites, need {MIN_GPS_SATELLITES}",
            Severity.BLOCK,
        ))

    if state.battery_pct is None:
        v.append(SafetyViolation("battery_unknown", "Battery level unknown", Severity.BLOCK))
    elif state.battery_pct < env.min_battery_arm_pct:
        v.append(SafetyViolation(
            "battery_low",
            f"Battery {state.battery_pct:.0f}% is below arm minimum {env.min_battery_arm_pct:.0f}%",
            Severity.BLOCK,
        ))

    if not state.has_position:
        v.append(SafetyViolation("no_position", "No position fix available", Severity.BLOCK))
    else:
        gf = within_geofence(state.latitude, state.longitude, env)
        if gf:
            v.append(SafetyViolation(gf.code, f"Pre-arm: {gf.message}", Severity.BLOCK))

    return v


def check_mission_plan(
    waypoints: Sequence[dict[str, Any]], env: SafetyEnvelope
) -> list[SafetyViolation]:
    """Validate a mission before it is uploaded, so a bad plan never reaches the FC."""
    v: list[SafetyViolation] = []
    if not waypoints:
        return [SafetyViolation("empty_mission", "Mission has no waypoints", Severity.BLOCK)]

    for i, wp in enumerate(waypoints):
        lat, lon = wp.get("latitude"), wp.get("longitude")
        alt = wp.get("altitude_m")
        speed = wp.get("speed_ms")

        if lat is None or lon is None:
            v.append(SafetyViolation(
                "waypoint_no_position", f"Waypoint {i} has no position", Severity.BLOCK))
            continue

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            v.append(SafetyViolation(
                "waypoint_bad_coords", f"Waypoint {i} coordinates out of range", Severity.BLOCK))
            continue

        if alt is None:
            v.append(SafetyViolation(
                "waypoint_no_altitude", f"Waypoint {i} has no altitude", Severity.BLOCK))
        elif alt > env.max_altitude_m:
            v.append(SafetyViolation(
                "waypoint_altitude",
                f"Waypoint {i} altitude {alt}m exceeds ceiling {env.max_altitude_m}m",
                Severity.BLOCK,
            ))
        elif alt <= 0:
            v.append(SafetyViolation(
                "waypoint_altitude", f"Waypoint {i} altitude must be positive", Severity.BLOCK))

        if speed is not None and speed > env.max_speed_ms:
            v.append(SafetyViolation(
                "waypoint_speed",
                f"Waypoint {i} speed {speed}m/s exceeds limit {env.max_speed_ms}m/s",
                Severity.BLOCK,
            ))

        gf = within_geofence(lat, lon, env)
        if gf:
            v.append(SafetyViolation(gf.code, f"Waypoint {i}: {gf.message}", Severity.BLOCK))

        if env.home_lat is not None and env.home_lon is not None:
            d = haversine_m(env.home_lat, env.home_lon, lat, lon)
            if d > env.max_distance_m:
                v.append(SafetyViolation(
                    "waypoint_distance",
                    f"Waypoint {i} is {d:.0f}m from home, limit {env.max_distance_m:.0f}m",
                    Severity.BLOCK,
                ))

    return v


# ---------------------------------------------------------------------------
# In-flight guards
# ---------------------------------------------------------------------------

def check_in_flight(state: Any, env: SafetyEnvelope) -> list[SafetyViolation]:
    """Continuous monitoring. ABORT means the caller should trigger RTL."""
    v: list[SafetyViolation] = []

    if state.seconds_since_heartbeat > HEARTBEAT_TIMEOUT_S:
        # PX4's own link-loss failsafe is authoritative and configured on the FC.
        # This exists so the operator sees it and the event is recorded.
        v.append(SafetyViolation(
            "link_lost",
            f"No heartbeat for {state.seconds_since_heartbeat:.1f}s",
            Severity.ABORT,
        ))

    if state.battery_pct is not None:
        if state.battery_pct <= env.land_battery_pct:
            v.append(SafetyViolation(
                "battery_critical",
                f"Battery {state.battery_pct:.0f}% at or below land threshold",
                Severity.ABORT,
            ))
        elif state.battery_pct <= env.rtl_battery_pct:
            v.append(SafetyViolation(
                "battery_rtl",
                f"Battery {state.battery_pct:.0f}% at or below RTL threshold",
                Severity.ABORT,
            ))

    if state.altitude_rel_m is not None and state.altitude_rel_m > env.max_altitude_m:
        v.append(SafetyViolation(
            "altitude_exceeded",
            f"Altitude {state.altitude_rel_m:.0f}m exceeds ceiling {env.max_altitude_m:.0f}m",
            Severity.ABORT,
        ))

    if state.has_position:
        gf = within_geofence(state.latitude, state.longitude, env)
        if gf:
            v.append(gf)

        if env.home_lat is not None and env.home_lon is not None:
            d = haversine_m(env.home_lat, env.home_lon, state.latitude, state.longitude)
            if d > env.max_distance_m:
                v.append(SafetyViolation(
                    "distance_exceeded",
                    f"{d:.0f}m from home exceeds limit {env.max_distance_m:.0f}m",
                    Severity.ABORT,
                ))

    if state.groundspeed_ms is not None and state.groundspeed_ms > env.max_speed_ms * 1.2:
        v.append(SafetyViolation(
            "overspeed",
            f"Groundspeed {state.groundspeed_ms:.1f}m/s well above limit {env.max_speed_ms:.1f}m/s",
            Severity.WARN,
        ))

    return v


def worst_severity(violations: Sequence[SafetyViolation]) -> Severity | None:
    if any(v.severity == Severity.BLOCK for v in violations):
        return Severity.BLOCK
    if any(v.severity == Severity.ABORT for v in violations):
        return Severity.ABORT
    if any(v.severity == Severity.WARN for v in violations):
        return Severity.WARN
    return None


# ---------------------------------------------------------------------------
# Push limits down to the flight controller
# ---------------------------------------------------------------------------

async def apply_fc_safety_params(system: Any, env: SafetyEnvelope) -> dict[str, Any]:
    """Write the envelope to PX4 parameters so the FC enforces it independently.

    This is what makes the limits real. If this service dies mid-flight, these
    parameters are what still brings the aircraft home.
    """
    params: dict[str, float | int] = {
        # Battery failsafe: 2 = Return, 3 = Land
        "COM_LOW_BAT_ACT": 2,
        # Data link loss -> RTL, after this many seconds
        "NAV_DLL_ACT": 2,
        "COM_DL_LOSS_T": int(HEARTBEAT_TIMEOUT_S * 2),
        # RC loss -> RTL
        "NAV_RCL_ACT": 2,
        # Altitude ceiling, enforced by the FC
        "GF_MAX_VER_DIST": float(env.max_altitude_m),
        # Max horizontal distance from home
        "GF_MAX_HOR_DIST": float(env.max_distance_m),
        # Geofence breach action: 2 = RTL
        "GF_ACTION": 2,
        # Horizontal speed limit in mission and position modes
        "MPC_XY_VEL_MAX": float(env.max_speed_ms),
    }

    applied: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for name, value in params.items():
        try:
            if isinstance(value, int):
                await system.param.set_param_int(name, value)
            else:
                await system.param.set_param_float(name, value)
            applied[name] = value
        except Exception as exc:  # noqa: BLE001 - report, do not abort the whole set
            failed[name] = str(exc)
            log.warning("could not set FC param %s=%s: %s", name, value, exc)

    if failed:
        log.error("FC safety params incomplete: %s", ", ".join(failed))

    return {"applied": applied, "failed": failed, "all_applied": not failed}
