from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Mapping

from ..models import (
    Availability,
    BatteryState,
    BatteryStatus,
    Health,
    Mode,
    PoseReference,
    RobotManifest,
    RobotState,
    Safety,
)
from .base import FieldMapping, FieldMappingStatus, MappingReport
from .mapped_adapter import MappedStandardsAdapter


def _status(value: Any) -> tuple[Mode, Availability]:
    normalized = str(value or "").lower()
    if normalized in {"working", "active", "moving"}:
        return Mode.WORKING, Availability.BUSY
    if normalized in {"idle", "available"}:
        return Mode.IDLE, Availability.AVAILABLE
    if normalized in {"offline", "unavailable"}:
        return Mode.OFFLINE, Availability.UNAVAILABLE
    if normalized in {"faulted", "error"}:
        return Mode.FAULTED, Availability.DEGRADED
    return Mode.WAITING, Availability.DEGRADED


class MassRoboticsAdapter(MappedStandardsAdapter):
    standard = "massrobotics"
    standard_version = "1.0"

    def ingest_manifest(
        self, document: Mapping[str, Any], observed_at_ms: int
    ) -> tuple[RobotManifest, MappingReport]:
        external_id = str(document.get("uuid", ""))
        if external_id != self.external_id:
            raise ValueError("MassRobotics uuid does not match configured external identity")
        fields = (
            FieldMapping("uuid", FieldMappingStatus.MAPPED),
            FieldMapping("manufacturerName", FieldMappingStatus.MAPPED),
            FieldMapping("robotModel", FieldMappingStatus.MAPPED),
            FieldMapping("robotType", FieldMappingStatus.MAPPED),
            FieldMapping("capabilities", FieldMappingStatus.UNSUPPORTED, "not part of UMP capability contract"),
        )
        manifest = replace(
            self.manifest(),
            manufacturer=str(document.get("manufacturerName") or "Unknown manufacturer"),
            model=str(document.get("robotModel") or "Unknown model"),
            robot_class=str(document.get("robotType") or "mobile_robot").replace(" ", "_"),
        )
        report = MappingReport(
            self.standard, self.standard_version, "external_to_ump", external_id,
            observed_at_ms, fields,
        )
        self._store_manifest(manifest, report)
        return manifest, report

    def ingest_state(
        self, document: Mapping[str, Any], observed_at_ms: int
    ) -> tuple[RobotState, MappingReport]:
        if str(document.get("uuid", "")) != self.external_id:
            raise ValueError("MassRobotics uuid does not match configured external identity")
        mode, _ = _status(document.get("operationalState"))
        fields: list[FieldMapping] = [
            FieldMapping("operationalState", FieldMappingStatus.MAPPED),
            FieldMapping("activity", FieldMappingStatus.MAPPED if document.get("taskId") else FieldMappingStatus.DEFAULTED, "idle semantic used when taskId is absent"),
            FieldMapping("intent", FieldMappingStatus.UNSUPPORTED, "standard does not provide semantic intent"),
            FieldMapping("progress", FieldMappingStatus.UNSUPPORTED, "standard does not provide normalized progress"),
        ]
        battery = None
        if "batteryPercentage" in document:
            level = float(document["batteryPercentage"]) / 100.0
            battery = BatteryState(
                level=level,
                status=BatteryStatus.CHARGING if document.get("batteryCharging") else BatteryStatus.DISCHARGING,
                observed_at_ms=observed_at_ms,
                estimated_runtime_s=(int(document["remainingRunTime"]) if document.get("remainingRunTime") is not None else None),
            )
            fields.append(FieldMapping("batteryPercentage", FieldMappingStatus.MAPPED))
        else:
            fields.append(FieldMapping("batteryPercentage", FieldMappingStatus.UNSUPPORTED, "not reported"))
        pose = None
        location = document.get("location")
        if isinstance(location, Mapping) and all(key in location for key in ("x", "y")):
            yaw = float(location.get("angle", 0.0))
            pose = PoseReference(
                frame_id=str(location.get("planarDatum") or "massrobotics-map"),
                position_m=(float(location["x"]), float(location["y"]), float(location.get("z", 0.0))),
                orientation_xyzw=(0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)),
                observed_at_ms=observed_at_ms,
            )
            fields.append(FieldMapping("location", FieldMappingStatus.MAPPED))
        else:
            fields.append(FieldMapping("location", FieldMappingStatus.UNSUPPORTED, "not reported"))
        errors = document.get("errors")
        health = Health.FAULTED if errors else Health.HEALTHY
        state = RobotState(
            robot_id=self.manifest().robot_id,
            mode=mode,
            safety=Safety.UNKNOWN,
            activity=str(document.get("taskId") or "No external task reported"),
            intent="Unknown: MassRobotics status does not provide semantic intent",
            progress=0.0,
            summary=f"MassRobotics AMR is {mode.value}",
            health=health,
            battery=battery,
            assignment_id=(str(document["taskId"]) if document.get("taskId") else None),
            pose=pose,
        )
        report = MappingReport(
            self.standard, self.standard_version, "external_to_ump", self.external_id,
            observed_at_ms, tuple(fields), correlation_id=state.assignment_id,
        )
        self._store_state(state, report)
        return state, report

    def export_manifest(self) -> tuple[dict[str, Any], MappingReport]:
        manifest = self.manifest()
        document = {
            "uuid": self.external_id,
            "manufacturerName": manifest.manufacturer,
            "robotModel": manifest.model,
            "robotType": manifest.robot_class,
        }
        report = MappingReport(
            self.standard, self.standard_version, "ump_to_external", manifest.robot_id,
            0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document),
        )
        return document, report

    def export_state(self) -> tuple[dict[str, Any], MappingReport]:
        state = self.state()
        document: dict[str, Any] = {
            "uuid": self.external_id,
            "operationalState": state.mode.value,
            "taskId": state.assignment_id,
        }
        fields = [FieldMapping(key, FieldMappingStatus.MAPPED) for key in document]
        if state.battery and state.battery.level is not None:
            document["batteryPercentage"] = state.battery.level * 100.0
            fields.append(FieldMapping("batteryPercentage", FieldMappingStatus.MAPPED))
        if state.pose:
            x, y, z = state.pose.position_m
            qx, qy, qz, qw = state.pose.orientation_xyzw
            document["location"] = {
                "x": x, "y": y, "z": z,
                "angle": math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)),
                "planarDatum": state.pose.frame_id,
            }
            fields.append(FieldMapping("location", FieldMappingStatus.MAPPED))
        report = MappingReport(
            self.standard, self.standard_version, "ump_to_external", state.robot_id,
            state.pose.observed_at_ms if state.pose else 0, tuple(fields),
            correlation_id=state.assignment_id,
        )
        return document, report

    def translate_external_task(self, document: Mapping[str, Any], **context: Any):
        return self._assignment_from_external(
            document,
            external_type=str(document.get("taskType", "")),
            **context,
        )
