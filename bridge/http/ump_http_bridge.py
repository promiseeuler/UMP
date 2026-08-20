#!/usr/bin/env python3
"""Allowlisted UMP local-adapter to vendor HTTP controller bridge."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import ssl
import time
from typing import Any
from urllib import error, parse, request


MAX_CONFIG_BYTES = 128 * 1024
MAX_CONTROLLER_RESPONSE_BYTES = 256 * 1024


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class CapabilityRoute:
    path: str
    cancel_path: str | None
    timeout_seconds: float
    maximum_input_bytes: int


@dataclass(frozen=True)
class BridgeConfig:
    ump_socket: str
    controller_base_url: str
    token_env: str | None
    ca_file: str | None
    client_certificate: str | None
    client_key: str | None
    allow_insecure_loopback: bool
    read_only: bool
    poll_interval_seconds: float
    capabilities: dict[str, CapabilityRoute]
    health_path: str | None = None
    observation_path: str | None = None

    @classmethod
    def load(cls, path: Path) -> "BridgeConfig":
        raw = path.read_bytes()
        if len(raw) > MAX_CONFIG_BYTES:
            raise BridgeError("bridge.config_too_large", "bridge config exceeds 128 KiB")
        value = json.loads(raw)
        allowed = {
            "ump_socket",
            "controller_base_url",
            "token_env",
            "ca_file",
            "client_certificate",
            "client_key",
            "allow_insecure_loopback",
            "read_only",
            "poll_interval_seconds",
            "capabilities",
            "health_path",
            "observation_path",
        }
        unknown = set(value) - allowed
        if unknown:
            raise BridgeError("bridge.config_unknown_field", f"unknown fields: {sorted(unknown)}")
        base_url = str(value["controller_base_url"]).rstrip("/")
        parsed = parse.urlparse(base_url)
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        insecure_allowed = bool(value.get("allow_insecure_loopback", False)) and loopback
        if parsed.scheme != "https" and not (parsed.scheme == "http" and insecure_allowed):
            raise BridgeError(
                "bridge.insecure_controller",
                "controller URL must use HTTPS; HTTP is allowed only for explicit loopback development",
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BridgeError("bridge.invalid_controller_url", "controller URL contains forbidden components")
        routes: dict[str, CapabilityRoute] = {}
        for capability, route in value.get("capabilities", {}).items():
            if not capability or len(capability) > 256 or not isinstance(route, dict):
                raise BridgeError("bridge.invalid_capability", "capability route is invalid")
            path_value = cls._validate_path(route.get("path"), capability)
            cancel = route.get("cancel_path")
            cancel_path = cls._validate_path(cancel, capability) if cancel is not None else None
            timeout = float(route.get("timeout_seconds", 30.0))
            maximum_input = int(route.get("maximum_input_bytes", 64 * 1024))
            if not 0.05 <= timeout <= 300.0:
                raise BridgeError("bridge.invalid_timeout", f"invalid timeout for {capability}")
            if not 1 <= maximum_input <= 1024 * 1024:
                raise BridgeError("bridge.invalid_input_limit", f"invalid input limit for {capability}")
            routes[capability] = CapabilityRoute(path_value, cancel_path, timeout, maximum_input)
        poll_interval = float(value.get("poll_interval_seconds", 0.1))
        if not 0.01 <= poll_interval <= 5.0:
            raise BridgeError("bridge.invalid_poll_interval", "poll interval is outside bounds")
        token_env = value.get("token_env")
        if token_env is not None and (not isinstance(token_env, str) or not token_env.isidentifier()):
            raise BridgeError("bridge.invalid_token_env", "token_env must be an environment variable name")
        client_certificate = value.get("client_certificate")
        client_key = value.get("client_key")
        if bool(client_certificate) != bool(client_key):
            raise BridgeError(
                "bridge.invalid_client_identity",
                "client_certificate and client_key must be configured together",
            )
        if not loopback and not token_env and not client_certificate:
            raise BridgeError(
                "bridge.missing_controller_auth",
                "non-loopback controllers require a bearer token or mTLS client identity",
            )
        return cls(
            ump_socket=str(value["ump_socket"]),
            controller_base_url=base_url,
            token_env=token_env,
            ca_file=value.get("ca_file"),
            client_certificate=client_certificate,
            client_key=client_key,
            allow_insecure_loopback=bool(value.get("allow_insecure_loopback", False)),
            read_only=bool(value.get("read_only", False)),
            poll_interval_seconds=poll_interval,
            capabilities=routes,
            health_path=(
                cls._validate_path(value["health_path"], "health")
                if value.get("health_path") is not None
                else None
            ),
            observation_path=(
                cls._validate_path(value["observation_path"], "observation")
                if value.get("observation_path") is not None
                else None
            ),
        )

    @staticmethod
    def _validate_path(value: Any, capability: str) -> str:
        if not isinstance(value, str) or not value.startswith("/"):
            raise BridgeError("bridge.invalid_path", f"route for {capability} must be absolute-path relative")
        if ".." in value.split("/") or "://" in value or "?" in value or "#" in value:
            raise BridgeError("bridge.invalid_path", f"route for {capability} contains forbidden components")
        return value


class LocalApiClient:
    def __init__(self, socket_path: str, timeout_seconds: float = 2.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.socket_path), self.timeout_seconds
        )
        writer.write(
            json.dumps(
                {"version": 1, "method": method, "params": params},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        await writer.drain()
        try:
            line = await asyncio.wait_for(reader.readline(), self.timeout_seconds)
            if not line:
                raise BridgeError("bridge.ump_disconnected", "UMP local API closed without a response", True)
            response = json.loads(line)
            if response.get("error"):
                raise BridgeError("bridge.ump_rejected", str(response["error"]))
            return response.get("result", {})
        finally:
            writer.close()
            await writer.wait_closed()


class HttpController:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.token = os.environ.get(config.token_env) if config.token_env else None
        if config.token_env and not self.token:
            raise BridgeError("bridge.missing_token", f"required token environment {config.token_env} is empty")
        self.ssl_context = ssl.create_default_context(cafile=config.ca_file)
        if config.client_certificate and config.client_key:
            self.ssl_context.load_cert_chain(config.client_certificate, config.client_key)

    def call(
        self, route: CapabilityRoute, payload: dict[str, Any], timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        return self._request(route.path, payload, timeout_seconds or route.timeout_seconds)

    def cancel(self, route: CapabilityRoute, task_id: str) -> None:
        if route.cancel_path is None:
            raise BridgeError("bridge.cancellation_unsupported", "controller has no cancellation route")
        self._request(route.cancel_path, {"task_id": task_id}, min(route.timeout_seconds, 5.0))

    def probe(self) -> None:
        if self.config.health_path is None:
            raise BridgeError("bridge.health_unsupported", "controller has no health route")
        response = self._request(self.config.health_path, {}, 2.0)
        if response.get("status") != "healthy":
            raise BridgeError("bridge.controller_unhealthy", "controller is not healthy", True)

    def observe(self) -> dict[str, Any]:
        if self.config.observation_path is None:
            raise BridgeError("bridge.observation_unsupported", "controller has no observation route")
        return self._request(self.config.observation_path, {}, 2.0)

    def _request(self, path: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        outgoing = request.Request(
            self.config.controller_base_url + path,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            handlers: list[Any] = [_RejectRedirects()]
            if outgoing.full_url.startswith("https://"):
                handlers.append(request.HTTPSHandler(context=self.ssl_context))
            opener = request.build_opener(*handlers)
            with opener.open(outgoing, timeout=timeout_seconds) as response:
                raw = response.read(MAX_CONTROLLER_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            retryable = 500 <= exc.code < 600
            code = "bridge.controller_unavailable" if retryable else "bridge.controller_rejected"
            raise BridgeError(code, f"controller HTTP status {exc.code}", retryable) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise BridgeError("bridge.controller_unavailable", "controller request failed", True) from exc
        if len(raw) > MAX_CONTROLLER_RESPONSE_BYTES:
            raise BridgeError("bridge.response_too_large", "controller response exceeds 256 KiB")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeError("bridge.invalid_response", "controller returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise BridgeError("bridge.invalid_response", "controller response must be an object")
        return value


class HttpBridge:
    def __init__(self, config: BridgeConfig, api: LocalApiClient | Any, controller: HttpController) -> None:
        self.config = config
        self.api = api
        self.controller = controller

    async def run_once(self) -> bool:
        if self.config.read_only:
            await self.api.call("ping", {})
            return False
        task = (await self.api.call("next_task", {})).get("task")
        if task is None:
            return False
        await self._execute(task)
        return True

    async def _execute(self, task: dict[str, Any]) -> None:
        task_id = str(task.get("task_id", ""))
        capability = str(task.get("capability", ""))
        route = self.config.capabilities.get(capability)
        if route is None:
            await self._fail(task_id, "bridge.capability_not_mapped", False)
            return
        if bool(task.get("interruptible")) and route.cancel_path is None:
            await self._fail(task_id, "bridge.cancellation_unsupported", False)
            return
        try:
            raw_input = bytes(task.get("input", []))
        except (TypeError, ValueError) as exc:
            await self._fail(task_id, "bridge.invalid_task_input", False)
            return
        if len(raw_input) > route.maximum_input_bytes:
            await self._fail(task_id, "bridge.input_too_large", False)
            return
        if task.get("input_content_type") != "application/json":
            await self._fail(task_id, "bridge.unsupported_content_type", False)
            return
        try:
            controller_input = json.loads(raw_input)
        except json.JSONDecodeError:
            await self._fail(task_id, "bridge.invalid_task_input", False)
            return
        deadline_ms = int(task.get("deadline_ms", 0))
        now_ms = int(time.time() * 1000)
        if deadline_ms and deadline_ms <= now_ms:
            await self._fail(task_id, "bridge.deadline_exceeded", False)
            return
        payload = {
            "task_id": task_id,
            "capability": capability,
            "input": controller_input,
            "deadline_ms": deadline_ms,
            "correlation_id": str(task.get("correlation_id", "")),
        }
        await self.api.call(
            "progress",
            {"task_id": task_id, "progress_per_mille": 100, "stage": "vendor_controller_dispatched"},
        )
        timeout = route.timeout_seconds
        if deadline_ms:
            timeout = min(timeout, max(0.05, (deadline_ms - now_ms) / 1000))
        invocation = asyncio.create_task(
            asyncio.to_thread(self.controller.call, route, payload, timeout)
        )
        try:
            while not invocation.done():
                status = await self.api.call("task_status", {"task_id": task_id})
                if status.get("state") in {"cancel_pending", "cancelled"}:
                    await asyncio.to_thread(self.controller.cancel, route, task_id)
                    await self.api.call(
                        "complete",
                        {"task_id": task_id, "outcome": "cancelled", "error_code": "bridge.cancelled"},
                    )
                    invocation.cancel()
                    return
                await asyncio.sleep(self.config.poll_interval_seconds)
            response = await invocation
            await self._complete_from_controller(task_id, response)
        except BridgeError as exc:
            await self._fail(task_id, exc.code, exc.retryable)

    async def _complete_from_controller(self, task_id: str, response: dict[str, Any]) -> None:
        status = response.get("status")
        if status == "succeeded":
            output = json.dumps(response.get("output", {}), separators=(",", ":")).encode()
            if len(output) > MAX_CONTROLLER_RESPONSE_BYTES:
                raise BridgeError("bridge.response_too_large", "controller output exceeds limit")
            await self.api.call(
                "complete",
                {"task_id": task_id, "outcome": "succeeded", "output": list(output)},
            )
            return
        if status == "failed":
            code = str(response.get("error_code", "bridge.controller_failed"))
            if not code or len(code) > 256:
                code = "bridge.invalid_error_code"
            await self.api.call(
                "complete",
                {
                    "task_id": task_id,
                    "outcome": "failed",
                    "error_code": code,
                    "retryable": bool(response.get("retryable", False)),
                    "retry_after_ms": min(max(int(response.get("retry_after_ms", 0)), 0), 60_000),
                },
            )
            return
        raise BridgeError("bridge.invalid_response", "controller response has no terminal status")

    async def _fail(self, task_id: str, code: str, retryable: bool) -> None:
        await self.api.call(
            "complete",
            {
                "task_id": task_id,
                "outcome": "failed",
                "error_code": code,
                "retryable": retryable,
                "retry_after_ms": 1_000 if retryable else 0,
            },
        )


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


async def run(config: BridgeConfig, once: bool) -> None:
    api = LocalApiClient(config.ump_socket)
    bridge = HttpBridge(config, api, HttpController(config))
    while True:
        handled = await bridge.run_once()
        if once:
            return
        if not handled:
            await asyncio.sleep(config.poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(BridgeConfig.load(args.config), args.once))


if __name__ == "__main__":
    main()
