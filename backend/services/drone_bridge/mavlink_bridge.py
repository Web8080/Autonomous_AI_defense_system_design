"""
MAVLink message bridge: translates between internal commands and MAVLink.

Owns the MAVSDK System per aircraft, subscribes to telemetry streams,
and exposes a clean async interface for mission upload, arm/disarm, RTL, and
emergency stop. Nothing else in the service touches MAVSDK directly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .connection import VehicleLink, LinkRegistry, LinkState

log = logging.getLogger("drone_bridge.mavlink")


class MAVLinkBridge:
    """High-level interface for talking to one aircraft."""

    def __init__(self, link: VehicleLink) -> None:
        self.link = link
        self._telemetry_task: asyncio.Task | None = None
        self._position_task: asyncio.Task | None = None
        self._flight_mode_task: asyncio.Task | None = None
        self._armed_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None

    @property
    def system(self) -> Any:
        if self.link.system is None:
            raise RuntimeError(f"Vehicle {self.link.asset_id} not connected")
        return self.link.system

    # ------------------------------------------------------------------
    # Telemetry subscription
    # ------------------------------------------------------------------

    async def start_telemetry_stream(self) -> None:
        """Subscribe to all relevant MAVSDK telemetry streams and update VehicleState."""
        if self._telemetry_task and not self._telemetry_task.done():
            return

        self._telemetry_task = asyncio.create_task(self._subscribe_position())
        self._position_task = asyncio.create_task(self._subscribe_flight_mode())
        self._armed_task = asyncio.create_task(self._subscribe_armed())
        self._health_task = asyncio.create_task(self._subscribe_health())
        self.link.add_task(self._telemetry_task)
        self.link.add_task(self._position_task)
        self.link.add_task(self._armed_task)
        self.link.add_task(self._health_task)

    async def _subscribe_position(self) -> None:
        try:
            async for pos in self.system.telemetry.position():
                self.link.state.latitude = pos.latitude_deg
                self.link.state.longitude = pos.longitude_deg
                self.link.state.altitude_rel_m = pos.relative_altitude_m
                self.link.state.altitude_amsl_m = pos.absolute_altitude_m
                self.link.touch_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("position stream ended asset=%s err=%s", self.link.asset_id, exc)

    async def _subscribe_flight_mode(self) -> None:
        try:
            async for mode in self.system.telemetry.flight_mode():
                self.link.state.flight_mode = mode.name
                self.link.touch_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("flight_mode stream ended asset=%s err=%s", self.link.asset_id, exc)

    async def _subscribe_armed(self) -> None:
        try:
            async for armed in self.system.telemetry.armed():
                self.link.state.armed = armed
                self.link.touch_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("armed stream ended asset=%s err=%s", self.link.asset_id, exc)

    async def _subscribe_health(self) -> None:
        try:
            async for health in self.system.telemetry.health():
                self.link.state.health_all_ok = health.is_ok
                self.link.touch_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("health stream ended asset=%s err=%s", self.link.asset_id, exc)

    # ------------------------------------------------------------------
    # Battery and GPS
    # ------------------------------------------------------------------

    async def poll_battery(self) -> None:
        """Single-shot battery poll (for pre-arm checks)."""
        try:
            async for batt in self.system.telemetry.battery():
                self.link.state.battery_pct = batt.remaining_percent * 100.0
                self.link.state.battery_voltage = batt.voltage_v
                break
        except Exception as exc:
            log.warning("battery poll failed asset=%s err=%s", self.link.asset_id, exc)

    async def poll_gps(self) -> None:
        """Single-shot GPS poll."""
        try:
            async for gps in self.system.telemetry.gps_info():
                self.link.state.gps_fix_type = gps.fix_type.value
                self.link.state.gps_satellites = gps.satellites_used
                break
        except Exception as exc:
            log.warning("gps poll failed asset=%s err=%s", self.link.asset_id, exc)

    async def poll_in_air(self) -> None:
        """Single-shot in-air poll."""
        try:
            async for in_air in self.system.telemetry.in_air():
                self.link.state.in_air = in_air
                break
        except Exception as exc:
            log.warning("in_air poll failed asset=%s err=%s", self.link.asset_id, exc)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def arm(self) -> bool:
        """Arm the aircraft. Returns True on success."""
        if not self.link.is_usable:
            log.error("arm rejected: link not usable asset=%s", self.link.asset_id)
            return False
        try:
            await self.system.action.arm()
            log.info("armed asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("arm failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def disarm(self) -> bool:
        """Disarm the aircraft (only when on ground)."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.action.disarm()
            log.info("disarmed asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("disarm failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def takeoff(self, altitude_m: float = 10.0) -> bool:
        """Command takeoff to specified altitude."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.action.set_takeoff_altitude(altitude_m)
            await self.system.action.takeoff()
            log.info("takeoff asset=%s alt=%.1fm", self.link.asset_id, altitude_m)
            return True
        except Exception as exc:
            log.error("takeoff failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def land(self) -> bool:
        """Command landing at current position."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.action.land()
            log.info("land asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("land failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def rtl(self) -> bool:
        """Return to launch."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.action.return_to_launch()
            log.info("rtl asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("rtl failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def emergency_stop(self) -> bool:
        """Immediate disarm in flight. Use only when aircraft poses danger."""
        if not self.link.system:
            log.warning("emergency_stop: no system asset=%s", self.link.asset_id)
            return False
        try:
            await self.system.action.disarm()
            log.critical("emergency_stop: disarmed asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("emergency_stop failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def set_max_speed(self, speed_ms: float) -> bool:
        """Set maximum horizontal speed."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.param.set_param_float("MPC_XY_VEL_MAX", speed_ms)
            return True
        except Exception as exc:
            log.warning("set_max_speed failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def set_max_altitude(self, altitude_m: float) -> bool:
        """Set maximum altitude ceiling."""
        if not self.link.is_usable:
            return False
        try:
            await self.system.param.set_param_float("GF_MAX_VER_DIST", altitude_m)
            return True
        except Exception as exc:
            log.warning("set_max_altitude failed asset=%s err=%s", self.link.asset_id, exc)
            return False

    async def kill(self) -> bool:
        """Force-disarm regardless of state. Last resort."""
        if not self.link.system:
            return False
        try:
            await self.system.action.kill()
            log.critical("KILL sent asset=%s", self.link.asset_id)
            return True
        except Exception as exc:
            log.error("kill failed asset=%s err=%s", self.link.asset_id, exc)
            return False


class BridgeRegistry:
    """Maps asset_id -> MAVLinkBridge. One bridge per connected aircraft."""

    def __init__(self, link_registry: LinkRegistry) -> None:
        self._link_registry = link_registry
        self._bridges: dict[str, MAVLinkBridge] = {}
        self._lock = asyncio.Lock()

    def get(self, asset_id: str) -> MAVLinkBridge | None:
        return self._bridges.get(asset_id)

    async def create(self, asset_id: str) -> MAVLinkBridge | None:
        """Create a bridge for an already-connected vehicle link."""
        link = self._link_registry.get(asset_id)
        if link is None or link.link_state == LinkState.DISCONNECTED:
            return None
        bridge = MAVLinkBridge(link)
        async with self._lock:
            self._bridges[asset_id] = bridge
        return bridge

    async def remove(self, asset_id: str) -> None:
        async with self._lock:
            self._bridges.pop(asset_id, None)

    async def close_all(self) -> None:
        async with self._lock:
            self._bridges.clear()
