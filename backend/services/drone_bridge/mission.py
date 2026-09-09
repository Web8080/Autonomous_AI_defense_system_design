"""
Mission executor: converts a stored mission (waypoints) to a MAVSDK mission
and runs it on the aircraft.

The safety envelope is applied BEFORE the mission is uploaded to the flight
controller. PX4 parameters push the same limits down so they hold even if this
service dies mid-flight.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .connection import VehicleLink
from .safety import (
    SafetyEnvelope,
    check_mission_plan,
    check_in_flight,
    apply_fc_safety_params,
    worst_severity,
    Severity,
)

log = logging.getLogger("drone_bridge.mission")


class MissionStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    ARMING = "arming"
    EXECUTING = "executing"
    COMPLETED = "completed"
    RTL = "rtl"
    LANDED = "landed"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclass
class MissionPlan:
    """A mission as loaded from the missions DB, before upload."""
    mission_id: str | None
    asset_id: str
    flight_id: str | None
    waypoints: list[dict[str, Any]]
    envelope: SafetyEnvelope
    approved: bool = False
    approved_by: str | None = None


class MissionExecutor:
    """Stateful executor for one mission on one aircraft."""

    def __init__(
        self,
        link: VehicleLink,
        mission: MissionPlan,
        *,
        status_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.link = link
        self.mission = mission
        self.status: MissionStatus = MissionStatus.PENDING
        self._status_callback = status_callback
        self._stop = asyncio.Event()
        self._monitor_task: asyncio.Task | None = None
        self._progress: dict[str, Any] = {}

    def _set_status(self, status: MissionStatus, **extra: Any) -> None:
        self.status = status
        payload = {"asset_id": self.link.asset_id, "mission_id": self.mission.mission_id,
                   "flight_id": self.mission.flight_id, "status": status.value}
        payload.update(extra)
        if self._status_callback:
            self._status_callback(status.value, payload)
        log.info("mission status asset=%s flight=%s status=%s",
                 self.link.asset_id, self.mission.flight_id, status.value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> MissionStatus:
        """Execute the full mission lifecycle. Call once per flight."""
        violations = check_mission_plan(self.mission.waypoints, self.mission.envelope)
        if violations:
            self._set_status(MissionStatus.FAILED,
                             reason=violations[0].message,
                             violations=[v.to_dict() for v in violations])
            return self.status

        if not self.mission.approved:
            self._set_status(MissionStatus.FAILED, reason="Mission not approved by operator")
            return self.status

        try:
            await self._upload_mission()
            await self._apply_fc_params()
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            await self._arm_and_execute()
            # Mission finished; PX4 returns to launch automatically
            # (set_return_to_launch_after_mission(True)). Stop the watchdog.
            self._stop.set()
            if self._monitor_task:
                self._monitor_task.cancel()
                await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._set_status(MissionStatus.COMPLETED)
        except asyncio.CancelledError:
            self._set_status(MissionStatus.ABORTED, reason="cancelled")
            raise
        except Exception as exc:
            log.exception("mission run failed asset=%s", self.link.asset_id)
            self._set_status(MissionStatus.FAILED, reason=str(exc))
            await self._safely_rtl()
        return self.status

    async def cancel(self) -> None:
        """Abort the mission and return to launch."""
        self._stop.set()
        self._set_status(MissionStatus.ABORTED, reason="operator cancel")
        await self._safely_rtl()

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    async def _upload_mission(self) -> None:
        from mavsdk.mission import MissionItem, MissionPlan as SDKMissionPlan
        from mavsdk import System

        self._set_status(MissionStatus.UPLOADING)
        system: System = self.link.system

        items: list[MissionItem] = []
        for wp in self.mission.waypoints:
            items.append(MissionItem(
                latitude_deg=float(wp["latitude"]),
                longitude_deg=float(wp["longitude"]),
                relative_altitude_m=float(wp["altitude_m"]),
                speed_m_s=float(wp.get("speed_ms") or 0.0),
                is_fly_through=False,
                gimbal_pitch_deg=float(wp.get("gimbal_pitch_deg") or float("nan")),
                gimbal_yaw_deg=float("nan"),
                camera_action=int(wp.get("camera_action") or 0),
                loiter_time_s=float(wp.get("loiter_seconds") or 0.0),
                camera_photo_interval_s=float("nan"),
                acceptance_radius_m=float("nan"),
                yaw_deg=float(wp.get("heading_deg") or float("nan")),
                camera_photo_intensity=float("nan"),
                vehicle_action=int(wp.get("vehicle_action") or 1),
            ))

        plan = SDKMissionPlan(mission_items=items)
        await system.mission.set_return_to_launch_after_mission(True)
        await system.mission.upload_mission(plan)
        self._set_status(MissionStatus.UPLOADED)

    async def _apply_fc_params(self) -> None:
        result = await apply_fc_safety_params(self.link.system, self.mission.envelope)
        if not result["all_applied"]:
            log.error("FC safety params incomplete for asset=%s", self.link.asset_id)

    async def _arm_and_execute(self) -> None:
        from mavsdk.mission import MissionStatus as SDKStatus

        system = self.link.system
        self._set_status(MissionStatus.ARMING)

        try:
            async for armed in system.telemetry.armed():
                if armed:
                    break
                await system.action.arm()
        except Exception:
            await system.action.arm()

        self._set_status(MissionStatus.EXECUTING)
        try:
            async for mission_progress in system.mission.mission_progress():
                current = mission_progress.current
                total = mission_progress.total
                if total > 0:
                    self._progress["current"] = current
                    self._progress["total"] = total

                if not self._progress.get("started", False):
                    await system.mission.start_mission()
                    self._progress["started"] = True

                if current >= total and total > 0:
                    break
        except Exception as exc:
            log.error("mission execution error asset=%s err=%s", self.link.asset_id, exc)
            raise

        self._set_status(MissionStatus.COMPLETED)

    async def _monitor_loop(self) -> None:
        """In-flight safety monitoring: RTL on any ABORT violation."""
        while not self._stop.is_set():
            violations = check_in_flight(self.link.state, self.mission.envelope)
            sev = worst_severity(violations)
            if sev == Severity.ABORT:
                for v in violations:
                    log.error("in-flight violation asset=%s code=%s msg=%s",
                              self.link.asset_id, v.code, v.message)
                self._set_status(MissionStatus.RTL, reason=[v.to_dict() for v in violations])
                self._stop.set()
                await self._safely_rtl()
                return
            if sev == Severity.WARN:
                for v in violations:
                    log.warning("in-flight warning asset=%s code=%s msg=%s",
                                self.link.asset_id, v.code, v.message)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def _safely_rtl(self) -> None:
        try:
            await self.link.system.action.return_to_launch()
            log.info("RTL issued asset=%s", self.link.asset_id)
        except Exception as exc:
            log.error("RTL failed asset=%s err=%s", self.link.asset_id, exc)