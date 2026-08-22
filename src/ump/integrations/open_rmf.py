from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Mapping

from ..models import Health, Mode, PoseReference, RobotManifest, RobotState, Safety
from .base import FieldMapping, FieldMappingStatus, IntegrationUnavailableError, MappingReport
from .mapped_adapter import MappedStandardsAdapter


class OpenRmfAdapter(MappedStandardsAdapter):
    standard = "open_rmf"
    standard_version = "2"

    def ingest_manifest(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("name", "")) != self.external_id:
            raise ValueError("Open-RMF robot name does not match configured identity")
        manifest = replace(
            self.manifest(),
            manufacturer=str(document.get("fleet_name") or "Open-RMF fleet"),
            model=str(document.get("model") or "RMF participant"),
            robot_class="mobile_robot",
        )
        fields = tuple(FieldMapping(name, FieldMappingStatus.MAPPED) for name in ("name", "fleet_name", "model"))
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, fields)
        self._store_manifest(manifest, report)
        return manifest, report

    def ingest_state(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("name", "")) != self.external_id:
            raise ValueError("Open-RMF robot state identity does not match configured identity")
        location = document.get("location") or {}
        pose = None
        fields = [FieldMapping("mode", FieldMappingStatus.MAPPED)]
        if isinstance(location, Mapping) and all(key in location for key in ("x", "y", "yaw", "level_name")):
            yaw = float(location["yaw"])
            pose = PoseReference(
                frame_id=str(location["level_name"]),
                position_m=(float(location["x"]), float(location["y"]), 0.0),
                orientation_xyzw=(0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),
                observed_at_ms=observed_at_ms,
            )
            fields.append(FieldMapping("location", FieldMappingStatus.MAPPED))
        else:
            fields.append(FieldMapping("location", FieldMappingStatus.UNSUPPORTED, "RMF location incomplete"))
        mode_value = str(document.get("mode", "idle")).lower()
        mode = Mode.WORKING if mode_value in {"moving", "working", "charging"} else Mode.IDLE
        task_id = str(document.get("task_id") or "") or None
        state = RobotState(
            robot_id=self.manifest().robot_id,
            mode=mode,
            safety=Safety.UNKNOWN,
            activity=f"RMF task {task_id}" if task_id else "No RMF task reported",
            intent=str(document.get("destination") or "Unknown: RMF destination not disclosed"),
            progress=float(document.get("progress", 0.0)),
            summary=f"Open-RMF participant is {mode.value}",
            health=Health.DEGRADED if document.get("issues") else Health.HEALTHY,
            assignment_id=task_id,
            pose=pose,
        )
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, tuple(fields), correlation_id=task_id)
        self._store_state(state, report)
        return state, report

    def export_manifest(self):
        manifest = self.manifest()
        document = {"name": self.external_id, "fleet_name": manifest.manufacturer, "model": manifest.model, "capabilities": [item.name for item in manifest.capabilities]}
        report = MappingReport(self.standard, self.standard_version, "ump_to_external", manifest.robot_id, 0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document))
        return document, report

    def export_state(self):
        state = self.state()
        document: dict[str, Any] = {"name": self.external_id, "mode": state.mode.value, "task_id": state.assignment_id, "progress": state.progress}
        fields = [FieldMapping(key, FieldMappingStatus.MAPPED) for key in document]
        if state.pose:
            qx, qy, qz, qw = state.pose.orientation_xyzw
            document["location"] = {"x": state.pose.position_m[0], "y": state.pose.position_m[1], "yaw": math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)), "level_name": state.pose.frame_id}
            fields.append(FieldMapping("location", FieldMappingStatus.MAPPED))
        return document, MappingReport(self.standard, self.standard_version, "ump_to_external", state.robot_id, state.pose.observed_at_ms if state.pose else 0, tuple(fields), correlation_id=state.assignment_id)

    def translate_external_task(self, document: Mapping[str, Any], **context: Any):
        category = str(document.get("category", ""))
        description = document.get("description")
        payload = dict(description) if isinstance(description, Mapping) else dict(document)
        return self._assignment_from_external(payload, external_type=category, **context)

    @staticmethod
    def require_runtime():
        try:
            import rclpy
            import rmf_adapter
        except ImportError as error:
            raise IntegrationUnavailableError("Open-RMF bridge requires ROS 2 rclpy and rmf_adapter") from error
        return rclpy, rmf_adapter

    @staticmethod
    def delegated_domains() -> tuple[str, ...]:
        return ("traffic", "maps", "doors", "lifts", "facility_schedule")
