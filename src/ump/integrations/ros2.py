from __future__ import annotations

from typing import Any, Mapping

from ..models import RobotManifest, RobotState, payload
from ..ros2 import Ros2RobotAdapter
from ..transport import PROTOCOL_VERSION
from .base import FieldMapping, FieldMappingStatus, IntegrationUnavailableError, MappingReport


class Ros2SemanticBridge:
    """ROS 2 awareness encoding around the existing high-level action adapter."""

    standard = "ros2"
    standard_version = "0.1"

    def __init__(self, adapter: Ros2RobotAdapter) -> None:
        self.adapter = adapter

    def export_manifest(self, observed_at_ms: int = 0):
        manifest = self.adapter.manifest()
        document = {"protocol": PROTOCOL_VERSION, **payload(manifest)}
        report = MappingReport(self.standard, self.standard_version, "ump_to_external", manifest.robot_id, observed_at_ms, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document))
        return document, report

    def export_state(self, observed_at_ms: int):
        state = self.adapter.state()
        document = {"protocol": PROTOCOL_VERSION, "observed_at_ms": observed_at_ms, **payload(state)}
        report = MappingReport(self.standard, self.standard_version, "ump_to_external", state.robot_id, observed_at_ms, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in document), correlation_id=state.assignment_id)
        return document, report

    def ingest_manifest(self, document: Mapping[str, Any], observed_at_ms: int):
        if document.get("protocol") != PROTOCOL_VERSION:
            raise ValueError("ROS 2 awareness manifest protocol is unsupported")
        manifest = self.adapter.manifest()
        if document.get("robot_id") != manifest.robot_id:
            raise ValueError("ROS 2 awareness identity does not match adapter")
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", manifest.robot_id, observed_at_ms, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in ("protocol", "robot_id")))
        return manifest, report

    def ingest_state(self, document: Mapping[str, Any], observed_at_ms: int):
        if document.get("protocol") != PROTOCOL_VERSION or document.get("robot_id") != self.adapter.manifest().robot_id:
            raise ValueError("ROS 2 awareness state protocol or identity is invalid")
        state = self.adapter.state()
        report = MappingReport(self.standard, self.standard_version, "external_to_ump", state.robot_id, observed_at_ms, tuple(FieldMapping(key, FieldMappingStatus.MAPPED) for key in ("protocol", "robot_id")), correlation_id=state.assignment_id)
        return state, report

    @staticmethod
    def require_runtime():
        try:
            import rclpy
            from ump_interfaces.action import ExecuteCapability
            from ump_interfaces.msg import SemanticManifest, SemanticState
        except ImportError as error:
            raise IntegrationUnavailableError("ROS 2 bridge requires rclpy and built ump_interfaces") from error
        return rclpy, ExecuteCapability, SemanticManifest, SemanticState
