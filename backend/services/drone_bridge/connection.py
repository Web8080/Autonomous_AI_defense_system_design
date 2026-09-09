"""
MAVLink connection management.

One VehicleLink per aircraft. Owns the MAVSDK System, tracks link health, and
reconnects with backoff. Nothing else in the service talks to MAVSDK directly.

Link health matters more than it looks: loss of the heartbeat is the trigger for
the failsafe path, so `seconds_since_heartbeat` is treated as a first-class
signal rather than a debugging aid.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("drone_bridge.connection")

# If no heartbeat within this window, the link is considered lost and the
# in-flight guard escalates. PX4's own failsafe is configured separately and is
# the authoritative one; this is the second layer.
HEARTBEAT_TIMEOUT_S = 3.0
RECONNECT_BASE_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 30.0


class LinkState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"  # connected but heartbeat is stale
    FAILED = "failed"


@dataclass
class VehicleState:
    """Latest known state. Written by the telemetry subscriber, read by everyone."""
    asset_id: str
    org_id: str | None = None
    site_id: str | None = None
    flight_id: str | None = None

    latitude: float | None = None
    longitude: float | None = None
    altitude_rel_m: float | None = None
    altitude_amsl_m: float | None = None
    heading_deg: float | None = None
    groundspeed_ms: float | None = None
    vertical_speed_ms: float | None = None
    roll_deg: float | None = None
    pitch_deg: float | None = None
    yaw_deg: float | None = None

    battery_pct: float | None = None
    battery_voltage: float | None = None
    gps_fix_type: int | None = None
    gps_satellites: int | None = None

    flight_mode: str | None = None
    armed: bool = False
    in_air: bool = False
    health_all_ok: bool = False

    last_heartbeat: float = field(default_factory=time.monotonic)

    @property
    def seconds_since_heartbeat(self) -> float:
        return time.monotonic() - self.last_heartbeat

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "org_id": self.org_id,
            "site_id": self.site_id,
            "flight_id": self.flight_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude_rel_m": self.altitude_rel_m,
            "altitude_amsl_m": self.altitude_amsl_m,
            "heading_deg": self.heading_deg,
            "groundspeed_ms": self.groundspeed_ms,
            "vertical_speed_ms": self.vertical_speed_ms,
            "roll_deg": self.roll_deg,
            "pitch_deg": self.pitch_deg,
            "yaw_deg": self.yaw_deg,
            "battery_pct": self.battery_pct,
            "battery_voltage": self.battery_voltage,
            "gps_fix_type": self.gps_fix_type,
            "gps_satellites": self.gps_satellites,
            "flight_mode": self.flight_mode,
            "armed": self.armed,
            "in_air": self.in_air,
            "health_all_ok": self.health_all_ok,
            "seconds_since_heartbeat": round(self.seconds_since_heartbeat, 3),
        }


class VehicleLink:
    """A single aircraft's MAVLink connection and cached state."""

    def __init__(self, asset_id: str, system_address: str, mavsdk_port: int | None = None):
        self.asset_id = asset_id
        self.system_address = system_address
        self.mavsdk_port = mavsdk_port
        self.state = VehicleState(asset_id=asset_id)
        self.link_state = LinkState.DISCONNECTED
        self.system: Any = None  # mavsdk.System
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._connect_attempts = 0

    async def connect(self) -> None:
        """Connect, then hold the connection open, reconnecting on loss."""
        from mavsdk import System

        self._stop.clear()
        while not self._stop.is_set():
            try:
                self.link_state = LinkState.CONNECTING
                log.info("connecting asset=%s addr=%s", self.asset_id, self.system_address)

                self.system = System(port=self.mavsdk_port) if self.mavsdk_port else System()
                await self.system.connect(system_address=self.system_address)

                # MAVSDK reports connection via the connection_state stream.
                async for cs in self.system.core.connection_state():
                    if cs.is_connected:
                        break
                    if self._stop.is_set():
                        return

                self.link_state = LinkState.CONNECTED
                self.state.last_heartbeat = time.monotonic()
                self._connect_attempts = 0
                log.info("connected asset=%s", self.asset_id)
                return

            except Exception as exc:  # noqa: BLE001 - any failure means retry
                self._connect_attempts += 1
                delay = min(
                    RECONNECT_BASE_DELAY_S * (2 ** (self._connect_attempts - 1)),
                    RECONNECT_MAX_DELAY_S,
                )
                self.link_state = LinkState.FAILED
                log.warning(
                    "connect failed asset=%s attempt=%d retry_in=%.1fs err=%s",
                    self.asset_id, self._connect_attempts, delay, exc,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return  # stop requested during backoff
                except asyncio.TimeoutError:
                    continue

    def touch_heartbeat(self) -> None:
        self.state.last_heartbeat = time.monotonic()
        if self.link_state == LinkState.DEGRADED:
            self.link_state = LinkState.CONNECTED

    def refresh_link_state(self) -> LinkState:
        """Downgrade to DEGRADED when the heartbeat goes stale."""
        if self.link_state == LinkState.CONNECTED:
            if self.state.seconds_since_heartbeat > HEARTBEAT_TIMEOUT_S:
                self.link_state = LinkState.DEGRADED
                log.warning(
                    "heartbeat stale asset=%s age=%.1fs",
                    self.asset_id, self.state.seconds_since_heartbeat,
                )
        return self.link_state

    @property
    def is_usable(self) -> bool:
        """Commands are only accepted on a healthy link."""
        return self.refresh_link_state() == LinkState.CONNECTED

    def add_task(self, task: asyncio.Task) -> None:
        self._tasks.append(task)

    async def close(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.link_state = LinkState.DISCONNECTED
        log.info("closed asset=%s", self.asset_id)


class LinkRegistry:
    """All active vehicle links, keyed by asset_id."""

    def __init__(self) -> None:
        self._links: dict[str, VehicleLink] = {}
        self._lock = asyncio.Lock()

    async def add(self, link: VehicleLink) -> None:
        async with self._lock:
            existing = self._links.get(link.asset_id)
            if existing:
                await existing.close()
            self._links[link.asset_id] = link

    def get(self, asset_id: str) -> VehicleLink | None:
        return self._links.get(asset_id)

    def all(self) -> list[VehicleLink]:
        return list(self._links.values())

    async def remove(self, asset_id: str) -> None:
        async with self._lock:
            link = self._links.pop(asset_id, None)
        if link:
            await link.close()

    async def close_all(self) -> None:
        for link in self.all():
            await link.close()
        self._links.clear()
