from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from ..models import BatteryState, BatteryStatus, Health, Mode, RobotManifest, RobotState, Safety
from .base import FieldMapping, FieldMappingStatus, IntegrationUnavailableError, MappingReport, TaskAuthorizationError
from .mapped_adapter import MappedStandardsAdapter


class OpcUaRoboticsAdapter(MappedStandardsAdapter):
    standard = "opc_ua_robotics"
    standard_version = "1.02"

    def ingest_manifest(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("SerialNumber", "")) != self.external_id:
            raise ValueError("OPC UA SerialNumber does not match configured identity")
        manifest = replace(
            self.manifest(),
            manufacturer=str(document.get("Manufacturer") or "Unknown manufacturer"),
            model=str(document.get("Model") or "Unknown model"),
            robot_class=str(document.get("DeviceClass") or "industrial_robot").replace(" ", "_"),
        )
        fields = tuple(FieldMapping(name, FieldMappingStatus.MAPPED) for name in ("SerialNumber", "Manufacturer", "Model", "DeviceClass"))
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, fields)
        self._store_manifest(manifest, report)
        return manifest, report

    def ingest_state(self, document: Mapping[str, Any], observed_at_ms: int):
        if str(document.get("SerialNumber", "")) != self.external_id:
            raise ValueError("OPC UA state identity does not match configured identity")
        health_text = str(document.get("Health", "unknown")).lower()
        health = {"healthy": Health.HEALTHY, "normal": Health.HEALTHY, "degraded": Health.DEGRADED, "faulted": Health.FAULTED}.get(health_text, Health.UNKNOWN)
        mode_text = str(document.get("OperatingMode", "waiting")).lower()
        mode = {item.value: item for item in Mode}.get(mode_text, Mode.WAITING)
        battery = None
        fields = [FieldMapping("Health", FieldMappingStatus.MAPPED), FieldMapping("OperatingMode", FieldMappingStatus.MAPPED)]
        if document.get("BatteryLevel") is not None:
            battery = BatteryState(float(document["BatteryLevel"]) / 100.0, BatteryStatus.CHARGING if document.get("Charging") else BatteryStatus.DISCHARGING, observed_at_ms)
            fields.append(FieldMapping("BatteryLevel", FieldMappingStatus.MAPPED))
        else:
            fields.append(FieldMapping("BatteryLevel", FieldMappingStatus.UNSUPPORTED, "node not exposed"))
        state = RobotState(
            robot_id=self.manifest().robot_id,
            mode=mode,
            safety=Safety.UNKNOWN,
            activity=str(document.get("Activity") or "Unknown: activity node not exposed"),
            intent=str(document.get("Intent") or "Unknown: intent node not exposed"),
            progress=float(document.get("Progress", 0.0)),
            summary=str(document.get("Summary") or f"OPC UA robot is {mode.value}"),
            health=health,
            battery=battery,
            assignment_id=(str(document["AssignmentId"]) if document.get("AssignmentId") else None),
        )
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", self.external_id, observed_at_ms, tuple(fields), correlation_id=state.assignment_id)
        self._store_state(state, report)
        return state, report

    def export_manifest(self):
        manifest = self.manifest()
        document = {"SerialNumber": self.external_id, "Manufacturer": manifest.manufacturer, "Model": manifest.model, "DeviceClass": manifest.robot_class, "Capabilities": [item.name for item in manifest.capabilities]}
        return document, MappingReport(self.standard, self.standard_version, "ump_to_external", manifest.robot_id, 0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document))

    def export_state(self):
        state = self.state()
        document: dict[str, Any] = {"SerialNumber": self.external_id, "OperatingMode": state.mode.value, "Health": state.health.value, "Safety": state.safety.value, "Activity": state.activity, "Intent": state.intent, "Progress": state.progress, "Summary": state.summary, "AssignmentId": state.assignment_id}
        if state.battery and state.battery.level is not None:
            document.update(BatteryLevel=state.battery.level * 100, Charging=state.battery.status is BatteryStatus.CHARGING)
        report = MappingReport(self.standard, self.standard_version, "ump_to_external", state.robot_id, 0, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document), correlation_id=state.assignment_id)
        return document, report

    def translate_external_task(self, document: Mapping[str, Any], **context: Any):
        raise TaskAuthorizationError("OPC UA Robotics integration exposes no actuator or job-control methods")

    @staticmethod
    def require_runtime():
        try:
            import asyncua
        except ImportError as error:
            raise IntegrationUnavailableError("OPC UA integration requires asyncua") from error
        return asyncua


class OpcUaClientProfile:
    """Read-only node names consumed from an owner-approved OPC UA address space."""

    NODE_NAMES = ("SerialNumber", "Manufacturer", "Model", "DeviceClass", "OperatingMode", "Health", "BatteryLevel", "Charging", "Activity", "Intent", "Progress", "Summary", "AssignmentId")

    def __init__(self, adapter: OpcUaRoboticsAdapter) -> None:
        self.adapter = adapter

    def ingest_nodes(self, values: Mapping[str, Any], observed_at_ms: int):
        allowed = {name: values[name] for name in self.NODE_NAMES if name in values}
        return self.adapter.ingest_state(allowed, observed_at_ms)


class OpcUaServerProfile:
    """Authorized read-only UMP view; no writable motion or actuator methods."""

    def __init__(self, adapter: OpcUaRoboticsAdapter, disclosure: tuple[str, ...] = ()) -> None:
        self.adapter = adapter
        self.disclosure = frozenset(disclosure)

    def address_space(self) -> dict[str, Any]:
        manifest, _ = self.adapter.export_manifest()
        state, _ = self.adapter.export_state()
        combined = {**manifest, **state}
        if not self.disclosure:
            return combined
        return {key: value for key, value in combined.items() if key in self.disclosure}

    @property
    def methods(self) -> tuple[()]:
        return ()
