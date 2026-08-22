from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Mapping

from ..models import BatteryState, BatteryStatus, Health, Mode, PoseReference, RobotManifest, RobotState, Safety
from .base import FieldMapping, FieldMappingStatus, IntegrationUnavailableError, MappingReport
from .mapped_adapter import MappedStandardsAdapter


VDA_STATE_TO_MODE = {
    "AUTOMATIC": Mode.WORKING,
    "SEMIAUTOMATIC": Mode.WAITING,
    "SERVICE": Mode.PAUSED,
    "TEACHIN": Mode.PAUSED,
}


class Vda5050Adapter(MappedStandardsAdapter):
    standard = "vda5050"
    standard_version = "3.0.0"

    def ingest_manifest(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("serialNumber", "")) != self.external_id:
            raise ValueError("VDA 5050 serialNumber does not match configured identity")
        manifest = replace(
            self.manifest(),
            manufacturer=str(document.get("manufacturer") or "Unknown manufacturer"),
            model=str(document.get("seriesName") or "Unknown model"),
            robot_class="mobile_robot",
        )
        fields = tuple(
            FieldMapping(name, FieldMappingStatus.MAPPED)
            for name in ("serialNumber", "manufacturer", "seriesName")
        )
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, fields)
        self._store_manifest(manifest, report)
        return manifest, report

    def ingest_state(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("serialNumber", "")) != self.external_id:
            raise ValueError("VDA 5050 state identity does not match configured identity")
        errors = document.get("errors") or []
        safety_document = document.get("safetyState") or {}
        if safety_document.get("eStop") not in {None, "NONE"}:
            safety = Safety.EMERGENCY_STOP
        elif safety_document.get("fieldViolation"):
            safety = Safety.PROTECTIVE_STOP
        else:
            safety = Safety.NORMAL
        mode = VDA_STATE_TO_MODE.get(str(document.get("operatingMode", "")), Mode.WAITING)
        battery_document = document.get("batteryState") or {}
        battery = None
        fields = [
            FieldMapping("operatingMode", FieldMappingStatus.MAPPED),
            FieldMapping("safetyState", FieldMappingStatus.MAPPED),
            FieldMapping("errors", FieldMappingStatus.MAPPED),
        ]
        if "batteryCharge" in battery_document:
            battery = BatteryState(
                level=float(battery_document["batteryCharge"]) / 100.0,
                status=BatteryStatus.CHARGING if battery_document.get("charging") else BatteryStatus.DISCHARGING,
                observed_at_ms=observed_at_ms,
                estimated_runtime_s=(int(float(battery_document["reach"]) * 60) if battery_document.get("reach") is not None else None),
            )
            fields.append(FieldMapping("batteryState", FieldMappingStatus.MAPPED))
        else:
            fields.append(FieldMapping("batteryState", FieldMappingStatus.UNSUPPORTED, "batteryCharge absent"))
        pose = None
        position = document.get("agvPosition")
        if isinstance(position, Mapping) and position.get("positionInitialized") is not False:
            yaw = float(position.get("theta", 0.0))
            pose = PoseReference(
                frame_id=str(position.get("mapId") or "vda-map"),
                position_m=(float(position.get("x", 0)), float(position.get("y", 0)), 0.0),
                orientation_xyzw=(0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)),
                observed_at_ms=observed_at_ms,
            )
            fields.append(FieldMapping("agvPosition", FieldMappingStatus.MAPPED))
        order_id = str(document.get("orderId") or "") or None
        last_node = str(document.get("lastNodeId") or "")
        state = RobotState(
            robot_id=self.manifest().robot_id,
            mode=Mode.FAULTED if errors else mode,
            safety=safety,
            activity=f"VDA order {order_id}" if order_id else "No VDA order reported",
            intent=f"Proceed from {last_node}" if last_node else "Unknown: no VDA route context",
            progress=0.0,
            summary=f"VDA 5050 mobile robot is {mode.value}",
            health=Health.FAULTED if errors else Health.HEALTHY,
            battery=battery,
            assignment_id=order_id,
            pose=pose,
        )
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, tuple(fields), correlation_id=order_id)
        self._store_state(state, report)
        return state, report

    def export_manifest(self):
        manifest = self.manifest()
        document = {"manufacturer": manifest.manufacturer, "serialNumber": self.external_id, "seriesName": manifest.model}
        return document, MappingReport(self.standard, self.standard_version, "ump_to_external", manifest.robot_id, 0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document))

    def export_state(self):
        state = self.state()
        document: dict[str, Any] = {
            "serialNumber": self.external_id,
            "orderId": state.assignment_id or "",
            "operatingMode": "AUTOMATIC" if state.mode is Mode.WORKING else "SEMIAUTOMATIC",
            "safetyState": {"eStop": "MANUAL" if state.safety is Safety.EMERGENCY_STOP else "NONE", "fieldViolation": state.safety is Safety.PROTECTIVE_STOP},
            "errors": [] if state.health is Health.HEALTHY else [{"errorType": "ump.health", "errorLevel": "WARNING"}],
        }
        if state.battery and state.battery.level is not None:
            document["batteryState"] = {"batteryCharge": state.battery.level * 100, "charging": state.battery.status is BatteryStatus.CHARGING}
        report = MappingReport(self.standard, self.standard_version, "ump_to_external", state.robot_id, 0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document), correlation_id=state.assignment_id)
        return document, report

    def translate_external_task(self, document: Mapping[str, Any], **context: Any):
        actions = document.get("actions") or []
        external_type = str(document.get("orderType") or (actions[0].get("actionType") if actions else ""))
        task_input = dict(document)
        task_input.setdefault("orderId", context["assignment_id"])
        return self._assignment_from_external(task_input, external_type=external_type, **context)

    @staticmethod
    def mqtt_client():
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:
            raise IntegrationUnavailableError("VDA 5050 MQTT transport requires paho-mqtt") from error
        return mqtt.Client()

    def topic(self, message: str, *, interface_name: str = "uagv", major_version: str = "v3") -> str:
        manifest = self.manifest()
        return f"{interface_name}/{major_version}/{manifest.manufacturer}/{self.external_id}/{message}"
