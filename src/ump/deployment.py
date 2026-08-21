from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from importlib.resources import files
import ipaddress
import os
from pathlib import Path
import ssl
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .credentials import SqliteCredentialStore
from .models import Mode, RobotManifest, RobotState, Safety
from .network import (
    IDENTITY_URI_PREFIX,
    TlsNetworkBus,
    certificate_sha256,
    load_network_config,
)
from .node import load_adapter
from .runtime import Participant, Registry


TOPOLOGY_PROFILE = "ump.deployment-topology/v1"
BUNDLE_PROFILE = "ump.deployment-bundle/v1"
LOCAL_REPORT_PROFILE = "ump.local-awareness-report/v1"
AWARENESS_MESSAGES = ("manifest", "state")


class DeploymentError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedReadOnlyAdapter:
    _manifest: RobotManifest
    _state: RobotState

    def manifest(self) -> RobotManifest:
        return self._manifest

    def state(self) -> RobotState:
        return self._state

    def accept(self, assignment) -> object:
        from .models import Outcome

        return Outcome(
            assignment.assignment_id,
            self._manifest.robot_id,
            False,
            "Generated read-only adapter rejects all assignments",
        )

    def cancel(self, assignment_id: str, reason: str) -> tuple[bool, str]:
        del assignment_id
        return False, f"Generated read-only adapter has no native work: {reason}"


def create_read_only_adapter(config_path: Path | None = None) -> GeneratedReadOnlyAdapter:
    if config_path is None:
        raise DeploymentError("generated read-only adapter requires a config path")
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
        required = {"robot_id", "manufacturer", "model", "robot_class"}
        if not isinstance(document, dict) or document.keys() != required:
            raise DeploymentError("generated adapter config fields are invalid")
        manifest = RobotManifest(
            document["robot_id"],
            document["manufacturer"],
            document["model"],
            document["robot_class"],
            (),
            adapter_version="0.1.0-local-lab",
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, DeploymentError):
            raise
        raise DeploymentError(f"generated adapter config is invalid: {error}") from error
    return GeneratedReadOnlyAdapter(
        manifest,
        RobotState(
            manifest.robot_id,
            Mode.IDLE,
            Safety.NORMAL,
            "Connected in awareness-only mode",
            "Publish semantic state and observe configured peers",
            0.0,
            f"{manifest.robot_class} {manifest.robot_id} is visible and read-only.",
        ),
    )


def deployment_topology_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("deployment_data/v1/topology.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _validate_topology(document: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    try:
        Draft202012Validator(deployment_topology_schema()).validate(document)
    except ValidationError as error:
        raise DeploymentError(f"deployment topology is invalid: {error.message}") from error
    nodes = tuple(document["nodes"])
    robot_ids = [node["robot_id"] for node in nodes]
    endpoints = [(node["host"], node["port"]) for node in nodes]
    if len(robot_ids) != len(set(robot_ids)):
        raise DeploymentError("deployment robot IDs must be unique")
    if len(endpoints) != len(set(endpoints)):
        raise DeploymentError("deployment host and port pairs must be unique")
    if document["mode"] == "local_lab":
        for node in nodes:
            try:
                address = ipaddress.ip_address(node["host"])
            except ValueError as error:
                raise DeploymentError("local-lab hosts must be loopback IP addresses") from error
            if not address.is_loopback:
                raise DeploymentError("local-lab hosts must be loopback IP addresses")
    return nodes


def _write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_private_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def _development_credentials(
    credentials_root: Path, nodes: tuple[dict[str, Any], ...]
) -> dict[str, dict[str, str]]:
    credentials_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UMP Local Lab CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = credentials_root / "local-lab-ca.pem"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    _write_private_key(credentials_root / "local-lab-ca.key", ca_key)

    generated: dict[str, dict[str, str]] = {}
    for node in nodes:
        robot_id = node["robot_id"]
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, robot_id)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"{IDENTITY_URI_PREFIX}{robot_id}")]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(True, False, True, False, False, False, False, False, False),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        certificate_path = credentials_root / f"{robot_id}.pem"
        private_key_path = credentials_root / f"{robot_id}.key"
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        _write_private_key(private_key_path, key)
        fingerprint = certificate_sha256(
            certificate.public_bytes(serialization.Encoding.DER)
        )
        generated[robot_id] = {
            "certificate": str(certificate_path),
            "private_key": str(private_key_path),
            "ca": str(ca_path),
            "fingerprint": fingerprint,
        }
    return generated


def _safe_output_root(output: str | Path) -> Path:
    root = Path(output).resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise DeploymentError("deployment output must not exist or must be an empty directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def generate_deployment_bundle(
    topology: str | Path | dict[str, Any],
    output: str | Path,
    *,
    development_pki: bool = False,
) -> dict[str, Any]:
    if isinstance(topology, (str, Path)):
        try:
            topology_path = Path(topology).resolve()
            document = json.loads(topology_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeploymentError("deployment topology is not readable JSON") from error
    else:
        document = topology
    if not isinstance(document, dict):
        raise DeploymentError("deployment topology must be an object")
    nodes = _validate_topology(document)
    if document["mode"] != "local_lab":
        raise DeploymentError("v1 generator currently supports local_lab mode only")
    if not development_pki:
        raise DeploymentError("local_lab generation requires explicit --development-pki")

    root = _safe_output_root(output)
    credentials = _development_credentials(root / "credentials", nodes)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1_000)
    generated_nodes = []
    for node in nodes:
        robot_id = node["robot_id"]
        node_root = root / "nodes" / robot_id
        node_root.mkdir(parents=True)
        adapter_config = node_root / "adapter.json"
        _write_json(
            adapter_config,
            {
                "robot_id": robot_id,
                "manufacturer": node["manufacturer"],
                "model": node["model"],
                "robot_class": node["robot_class"],
            },
        )
        credential = credentials[robot_id]

        credential_database = node_root / "state" / "credentials.sqlite3"
        credential_directory = node_root / "state" / "managed-credentials"
        store = SqliteCredentialStore(robot_id, credential_database, credential_directory)
        try:
            generation = store.enroll(
                credential["certificate"],
                credential["private_key"],
                credential["ca"],
                now_ms,
            )
            generation = store.activate(generation.generation, now_ms)
        finally:
            store.close()

        network = {
            "robot_id": robot_id,
            "bind_host": node["host"],
            "bind_port": node["port"],
            "certificate_path": os.path.relpath(generation.certificate_path, node_root),
            "private_key_path": os.path.relpath(generation.private_key_path, node_root),
            "ca_path": os.path.relpath(generation.ca_path, node_root),
            "replay_database_path": "state/replay.sqlite3",
            "inbox_database_path": "state/inbox.sqlite3",
            "outbox_database_path": "state/outbox.sqlite3",
            "peers": [
                {
                    "robot_id": peer["robot_id"],
                    "host": peer["host"],
                    "port": peer["port"],
                    "certificate_sha256": credentials[peer["robot_id"]]["fingerprint"],
                    "allowed_message_types": list(AWARENESS_MESSAGES),
                    "allowed_capabilities": [],
                }
                for peer in nodes
                if peer["robot_id"] != robot_id
            ],
        }
        network_path = node_root / "network.json"
        _write_json(network_path, network)
        config = load_network_config(network_path)

        node_arguments = (
            '  --network "$HERE/network.json" '
            "  --adapter ump.deployment:create_read_only_adapter "
            '  --adapter-config "$HERE/adapter.json" '
            '  --assignment-database "$HERE/state/assignments.sqlite3" '
            '  --authority-database "$HERE/state/authority.sqlite3" '
            '  --credential-database "$HERE/state/credentials.sqlite3" '
            '  --credential-directory "$HERE/state/managed-credentials" '
            '  --inspector-database "$HERE/state/inspector.sqlite3" '
            "  --state-hz 2\n"
        )
        script_header = (
            "#!/bin/sh\n"
            "set -eu\n"
            'HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        )
        run_path = node_root / "run.sh"
        run_path.write_text(
            script_header + "exec ump-node " + node_arguments, encoding="utf-8"
        )
        run_path.chmod(0o755)
        preflight_path = node_root / "preflight.sh"
        preflight_path.write_text(
            script_header + "exec ump-node --preflight " + node_arguments,
            encoding="utf-8",
        )
        preflight_path.chmod(0o755)
        generated_nodes.append(
            {
                "robot_id": robot_id,
                "network": str(network_path.relative_to(root)),
                "adapter_config": str(adapter_config.relative_to(root)),
                "launch": str(run_path.relative_to(root)),
                "preflight": str(preflight_path.relative_to(root)),
                "configured_peers": len(config.peers),
                "credential_fingerprint_sha256": credential["fingerprint"],
            }
        )

    bundle = {
        "profile": BUNDLE_PROFILE,
        "mode": "local_lab",
        "security": {
            "development_pki": True,
            "production_eligible": False,
            "warning": "Development credentials are short-lived and must never be used on physical deployments.",
        },
        "nodes": generated_nodes,
    }
    _write_json(root / "bundle.json", bundle)
    return bundle


def _bus_from_config(config_path: Path) -> TlsNetworkBus:
    config = load_network_config(config_path)
    return TlsNetworkBus.from_config(config)


def verify_local_awareness(bundle_root: str | Path) -> dict[str, Any]:
    root = Path(bundle_root).resolve()
    try:
        bundle = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError("deployment bundle is not readable") from error
    if bundle.get("profile") != BUNDLE_PROFILE or bundle.get("mode") != "local_lab":
        raise DeploymentError("deployment bundle profile is unsupported")
    if bundle.get("security", {}).get("production_eligible") is not False:
        raise DeploymentError("local verification requires a non-production bundle")

    buses: dict[str, TlsNetworkBus] = {}
    registries: dict[str, Registry] = {}
    participants: list[Participant] = []
    try:
        for node in bundle["nodes"]:
            robot_id = node["robot_id"]
            network_path = root / node["network"]
            bus = _bus_from_config(network_path)
            buses[robot_id] = bus
            registries[robot_id] = Registry(bus)
        for bus in buses.values():
            bus.start()
        for node in bundle["nodes"]:
            robot_id = node["robot_id"]
            adapter = load_adapter(
                "ump.deployment:create_read_only_adapter",
                root / node["adapter_config"],
            )
            participant = Participant(
                adapter,
                buses[robot_id],
                clock_ms=lambda: 1_000,
            )
            participants.append(participant)
        for participant in participants:
            participant.announce(1_000)

        robot_ids = sorted(buses)
        observations = {
            observer_id: {
                peer_id: {
                    "manifest": registry.peers.get(peer_id) is not None
                    and registry.peers[peer_id].manifest is not None,
                    "state": registry.peers.get(peer_id) is not None
                    and registry.peers[peer_id].state is not None,
                }
                for peer_id in robot_ids
            }
            for observer_id, registry in registries.items()
        }
        all_manifests = all(
            evidence["manifest"]
            for peers in observations.values()
            for evidence in peers.values()
        )
        all_states = all(
            evidence["state"]
            for peers in observations.values()
            for evidence in peers.values()
        )
        no_delivery_errors = all(not bus.errors for bus in buses.values())
        report = {
            "profile": LOCAL_REPORT_PROFILE,
            "passed": False,
            "transport": "mutual_tls_tcp_loopback",
            "production_qualification": False,
            "robot_count": len(robot_ids),
            "observations": observations,
            "checks": {
                "all_manifests_observed": all_manifests,
                "all_states_observed": all_states,
                "no_delivery_errors": no_delivery_errors,
                "read_only_capabilities": all(
                    not participant.adapter.manifest().capabilities
                    for participant in participants
                ),
                "peer_fingerprints_pinned": all(
                    all(peer.certificate_sha256 for peer in load_network_config(root / node["network"]).peers)
                    for node in bundle["nodes"]
                ),
            },
        }
        report["passed"] = bool(report["checks"]) and all(report["checks"].values())
        _write_json(root / "local-awareness-report.json", report)
        return report
    except (OSError, ssl.SSLError, ValueError) as error:
        if isinstance(error, DeploymentError):
            raise
        raise DeploymentError(f"local awareness verification failed: {error}") from error
    finally:
        for participant in participants:
            participant.close()
        for bus in buses.values():
            bus.stop()


def default_local_topology(robot_count: int = 3, base_port: int = 17443) -> dict[str, Any]:
    if not 2 <= robot_count <= 8:
        raise DeploymentError("local topology requires between 2 and 8 robots")
    if not 1_024 <= base_port <= 65_535 - robot_count:
        raise DeploymentError("local topology base port is invalid")
    classes = (
        ("humanoid", "humanoid"),
        ("quadruped", "quadruped"),
        ("mobile-arm", "mobile_arm"),
        ("mobile-base", "mobile_base"),
    )
    return {
        "profile": TOPOLOGY_PROFILE,
        "mode": "local_lab",
        "nodes": [
            {
                "robot_id": f"robot-{classes[index % len(classes)][0]}-{index + 1}",
                "manufacturer": "Local Lab",
                "model": f"SIM-{index + 1}",
                "robot_class": classes[index % len(classes)][1],
                "host": "127.0.0.1",
                "port": base_port + index,
            }
            for index in range(robot_count)
        ],
    }
