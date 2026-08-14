#!/usr/bin/env python3
"""Supervise an external UMP gateway for one explicitly associated machine."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_MODULE_PATH = ROOT / "bridge" / "http" / "ump_http_bridge.py"
SPEC = importlib.util.spec_from_file_location("ump_http_bridge", BRIDGE_MODULE_PATH)
http_bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
import sys
sys.modules[SPEC.name] = http_bridge
SPEC.loader.exec_module(http_bridge)

MAX_GATEWAY_CONFIG_BYTES = 128 * 1024
VALID_OPERATIONAL = {"starting", "idle", "busy", "paused", "degraded", "stopping", "faulted"}
VALID_SAFETY = {"normal", "protective_stop", "emergency_stop", "recovery_required", "unknown"}


@dataclass(frozen=True)
class GatewayConfig:
    gateway_id: str
    represented_machine_id: str
    controller_interface: str
    runtime_config: Path
    bridge_config: Path
    status_file: Path

    @classmethod
    def load(cls, path: Path) -> "GatewayConfig":
        raw = path.read_bytes()
        if len(raw) > MAX_GATEWAY_CONFIG_BYTES:
            raise http_bridge.BridgeError("gateway.config_too_large", "gateway config exceeds 128 KiB")
        value = json.loads(raw)
        allowed = {
            "gateway_id", "represented_machine_id", "controller_interface",
            "runtime_config", "bridge_config", "status_file",
        }
        unknown = set(value) - allowed
        if unknown:
            raise http_bridge.BridgeError("gateway.config_unknown_field", f"unknown fields: {sorted(unknown)}")
        gateway_id = str(value["gateway_id"])
        machine_id = str(value["represented_machine_id"])
        interface = str(value["controller_interface"])
        if not gateway_id.startswith("ump:gateway:"):
            raise http_bridge.BridgeError("gateway.invalid_id", "gateway_id must start with ump:gateway:")
        if not machine_id.startswith("ump:machine:"):
            raise http_bridge.BridgeError(
                "gateway.invalid_machine_id", "represented_machine_id must start with ump:machine:"
            )
        if not interface or len(interface) > 128:
            raise http_bridge.BridgeError(
                "gateway.invalid_interface", "controller_interface is required and bounded"
            )
        return cls(
            gateway_id, machine_id, interface, Path(value["runtime_config"]),
            Path(value["bridge_config"]), Path(value["status_file"]),
        )

    def configure_runtime(self, bridge: Any) -> None:
        value = json.loads(self.runtime_config.read_bytes())
        if value.get("machine_id") != self.represented_machine_id:
            raise http_bridge.BridgeError(
                "gateway.identity_mismatch", "runtime machine identity does not match gateway association"
            )
        value["gateway_proxy"] = {
            "gateway_id": self.gateway_id,
            "represented_machine_id": self.represented_machine_id,
            "controller_interface": self.controller_interface,
            "read_only": bridge.read_only,
        }
        value["protocol_minimum_minor"] = 2
        if int(value.get("protocol_minor", 0)) < 2:
            raise http_bridge.BridgeError(
                "gateway.protocol_too_old", "gateway proxy requires protocol 1.2 or newer"
            )
        encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        temporary = self.runtime_config.with_name(f".{self.runtime_config.name}.gateway.tmp")
        temporary.write_bytes(encoded)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.runtime_config)


class GatewaySupervisor:
    def __init__(self, config: GatewayConfig, bridge_config: Any, api: Any, controller: Any) -> None:
        if bridge_config.read_only and bridge_config.observation_path is None:
            raise http_bridge.BridgeError(
                "gateway.observation_required", "read-only gateway requires an observation route"
            )
        if not bridge_config.read_only and bridge_config.health_path is None:
            raise http_bridge.BridgeError(
                "gateway.health_required", "command gateway requires a health route"
            )
        self.config = config
        self.bridge_config = bridge_config
        self.api = api
        self.controller = controller
        self.bridge = http_bridge.HttpBridge(bridge_config, api, controller)
        self.revision = self._previous_revision() + 1

    def _previous_revision(self) -> int:
        try:
            return int(json.loads(self.config.status_file.read_bytes()).get("revision", 0))
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    async def cycle(self) -> bool:
        try:
            ping = await self.api.call("ping", {})
            if ping.get("machine_id") != self.config.represented_machine_id:
                raise http_bridge.BridgeError(
                    "gateway.identity_mismatch", "local runtime identity does not match represented machine"
                )
            self.revision = max(self.revision, int(ping.get("state_revision", 0)) + 1)
            proxy = ping.get("proxy")
            if not isinstance(proxy, dict) or proxy.get("gateway_id") != self.config.gateway_id:
                raise http_bridge.BridgeError(
                    "gateway.association_mismatch", "runtime does not advertise this gateway association"
                )
            if self.bridge_config.read_only:
                observation = await asyncio.to_thread(self.controller.observe)
                await self._publish_observation(observation)
                self._write_status("connected", "observing", None)
                return False
            await asyncio.to_thread(self.controller.probe)
            await self._publish_state("idle", "unknown", [])
            self._write_status("connected", "command", None)
            return await self.bridge.run_once()
        except http_bridge.BridgeError as exc:
            if exc.code == "gateway.identity_mismatch":
                self._write_status("identity_mismatch", "disabled", exc.code)
                raise
            try:
                await self._publish_state("degraded", "unknown", [exc.code])
            except Exception:
                pass
            state = "runtime_disconnected" if exc.code.startswith("bridge.ump_") else "controller_disconnected"
            self._write_status(state, "disabled", exc.code)
            return False
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            self._write_status("runtime_disconnected", "disabled", type(exc).__name__)
            return False

    async def _publish_observation(self, value: dict[str, Any]) -> None:
        operational = str(value.get("operational", "degraded"))
        safety = str(value.get("safety", "unknown"))
        health = value.get("health_codes", [])
        if operational not in VALID_OPERATIONAL or safety not in VALID_SAFETY:
            raise http_bridge.BridgeError(
                "gateway.invalid_observation", "controller observation contains invalid state"
            )
        if not isinstance(health, list) or any(not isinstance(code, str) for code in health):
            raise http_bridge.BridgeError(
                "gateway.invalid_observation", "controller health codes must be strings"
            )
        await self._publish_state(operational, safety, health[:64])

    async def _publish_state(self, operational: str, safety: str, health_codes: list[str]) -> None:
        await self.api.call("state_update", {
            "operational": operational,
            "safety": safety,
            "revision": self.revision,
            "source_time_ms": int(time.time() * 1000),
            "component": "external_gateway",
            "health_codes": health_codes,
        })
        self.revision += 1

    def _write_status(self, connectivity: str, mode: str, fault: str | None) -> None:
        value = {
            "version": 1,
            "gateway_id": self.config.gateway_id,
            "represented_machine_id": self.config.represented_machine_id,
            "controller_interface": self.config.controller_interface,
            "proxy": True,
            "read_only": self.bridge_config.read_only,
            "connectivity": connectivity,
            "mode": mode,
            "fault": fault,
            "revision": self.revision,
            "observed_at_ms": int(time.time() * 1000),
        }
        self.config.status_file.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = self.config.status_file.with_name(f".{self.config.status_file.name}.tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        os.chmod(temporary, 0o640)
        os.replace(temporary, self.config.status_file)


async def run(supervisor: GatewaySupervisor, once: bool) -> None:
    while True:
        await supervisor.cycle()
        if once:
            return
        await asyncio.sleep(supervisor.bridge_config.poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--configure-runtime", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = GatewayConfig.load(args.config)
    bridge_config = http_bridge.BridgeConfig.load(config.bridge_config)
    if args.configure_runtime:
        config.configure_runtime(bridge_config)
        return
    api = http_bridge.LocalApiClient(bridge_config.ump_socket)
    controller = http_bridge.HttpController(bridge_config)
    asyncio.run(run(GatewaySupervisor(config, bridge_config, api, controller), args.once))


if __name__ == "__main__":
    main()
