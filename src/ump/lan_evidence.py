"""Validation for integrity-bound two-host UMP LAN benchmark evidence."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import ipaddress
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class LanEvidenceValidationError(ValueError):
    pass


def lan_evidence_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("lan_evidence_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LanEvidenceValidationError(
            f"{description} cannot be read: {error}"
        ) from error
    if not isinstance(document, dict):
        raise LanEvidenceValidationError(f"{description} must contain a JSON object")
    return document


def _artifact(base: Path, evidence: dict[str, str], description: str) -> Path:
    path = (base / evidence["artifact"]).resolve()
    if not path.is_relative_to(base):
        raise LanEvidenceValidationError(f"{description} escapes the evidence bundle")
    if not path.is_file():
        raise LanEvidenceValidationError(f"{description} does not exist")
    actual = sha256(path.read_bytes()).hexdigest()
    if actual != evidence["sha256"]:
        raise LanEvidenceValidationError(f"{description} digest does not match")
    return path


def _require(value: bool, message: str) -> None:
    if not value:
        raise LanEvidenceValidationError(message)


def validate_lan_evidence_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Validate report integrity and cross-host benchmark invariants."""
    path = Path(manifest_path)
    document = _read_json(path, "LAN evidence manifest")
    errors = sorted(
        Draft202012Validator(lan_evidence_schema()).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise LanEvidenceValidationError(f"{location}: {error.message}")

    base = path.resolve().parent
    client_path = _artifact(base, document["client_report"], "client report")
    server_path = _artifact(base, document["server_report"], "server report")
    _require(client_path != server_path, "client and server reports must be distinct")
    client = _read_json(client_path, "client report")
    server = _read_json(server_path, "server report")

    _require(
        client.get("profile") == "ump.reference.tls-network/v1",
        "client report has an unsupported profile",
    )
    _require(
        server.get("profile") == "ump.reference.tls-network-server/v1",
        "server report has an unsupported profile",
    )
    _require(client.get("passed") is True, "client benchmark did not pass")
    _require(server.get("passed") is True, "server benchmark did not pass")
    checks = client.get("checks")
    _require(
        isinstance(checks, dict)
        and bool(checks)
        and all(value is True for value in checks.values()),
        "client benchmark checks did not all pass",
    )

    client_environment = client.get("environment")
    server_environment = server.get("environment")
    _require(
        isinstance(client_environment, dict)
        and isinstance(server_environment, dict),
        "benchmark reports must include environments",
    )
    client_hostname = client_environment.get("local_hostname")
    server_hostname = server_environment.get("hostname")
    _require(
        isinstance(client_hostname, str) and bool(client_hostname.strip()),
        "client report lacks a hostname",
    )
    _require(
        isinstance(server_hostname, str) and bool(server_hostname.strip()),
        "server report lacks a hostname",
    )
    _require(
        client_hostname.casefold() != server_hostname.casefold(),
        "LAN evidence must come from two distinct hostnames",
    )

    _require(
        client.get("remote_robot_id") == server.get("robot_id"),
        "client peer identity does not match the server identity",
    )
    _require(
        isinstance(server.get("bind_port"), int),
        "server report has an invalid bind port",
    )
    _require(
        client.get("remote_port") == server.get("bind_port"),
        "client destination port does not match the server port",
    )
    samples = client.get("samples")
    warmup = client.get("warmup_samples")
    _require(
        isinstance(samples, int) and samples >= 10,
        "client report has an invalid sample count",
    )
    _require(
        isinstance(warmup, int) and warmup >= 0,
        "client report has an invalid warmup count",
    )
    _require(
        server.get("expected_messages") == samples + warmup
        and server.get("received_messages") == samples + warmup,
        "server message counts do not match the client run",
    )
    remote_host = client.get("remote_host")
    _require(isinstance(remote_host, str), "client report lacks a remote host")
    try:
        remote_address = ipaddress.ip_address(remote_host)
    except ValueError:
        remote_address = None
    _require(
        remote_host.casefold() != "localhost"
        and (remote_address is None or not remote_address.is_loopback),
        "LAN evidence must not use a loopback destination",
    )
    round_trip_p95_ms = client.get("round_trip_p95_ms")
    _require(
        isinstance(round_trip_p95_ms, (int, float))
        and not isinstance(round_trip_p95_ms, bool)
        and round_trip_p95_ms >= 0,
        "client report has an invalid p95 measurement",
    )

    return {
        "valid": True,
        "validation_scope": "artifact_integrity_and_cross_host_report_consistency",
        "protocol": document["protocol"],
        "repository_revision": document["repository_revision"],
        "run_id": document["run_id"],
        "client_hostname": client_hostname,
        "server_hostname": server_hostname,
        "samples": samples,
        "round_trip_p95_ms": round_trip_p95_ms,
        "evidence_artifacts_verified": 2,
    }
