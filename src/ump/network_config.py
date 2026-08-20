"""Public schema and validation report for UMP network configuration."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from typing import Any

from .network import load_network_config


def network_config_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("network_config_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_network_config(path: str | Path) -> dict[str, Any]:
    """Load a strict config and emit a non-secret deployment summary."""
    config = load_network_config(path)
    return {
        "valid": True,
        "profile": "ump.network-config/v1",
        "robot_id": config.robot_id,
        "bind_host": config.bind_host,
        "bind_port": config.bind_port,
        "configured_peers": len(config.peers),
        "peer_policies": [
            {
                "robot_id": peer.robot_id,
                "certificate_pinned": peer.certificate_sha256 is not None,
                "allowed_message_types": list(peer.allowed_message_types),
                "allowed_capabilities": list(peer.allowed_capabilities),
            }
            for peer in config.peers
        ],
        "delivery": {
            "maximum_pending_deliveries": config.maximum_pending_deliveries,
            "reserved_safety_deliveries": config.reserved_safety_deliveries,
            "timeout_seconds": config.timeout,
        },
    }
