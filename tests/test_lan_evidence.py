from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ump.cli import lan_evidence_main
from ump.lan_evidence import (
    LanEvidenceValidationError,
    validate_lan_evidence_bundle,
)


ROOT = Path(__file__).parents[1]
REVISION = "a" * 40


def write_json(path: Path, document: dict) -> dict[str, str]:
    content = json.dumps(document, sort_keys=True)
    path.write_text(content, encoding="utf-8")
    return {"artifact": path.name, "sha256": sha256(content.encode()).hexdigest()}


def reports() -> tuple[dict, dict]:
    client = {
        "profile": "ump.reference.tls-network/v1",
        "repository_revision": REVISION,
        "samples": 1000,
        "warmup_samples": 64,
        "local_robot_id": "benchmark-client",
        "remote_robot_id": "benchmark-server",
        "remote_host": "192.0.2.10",
        "remote_port": 7443,
        "round_trip_p50_ms": 3.0,
        "round_trip_p95_ms": 4.5,
        "round_trip_p99_ms": 8.0,
        "round_trips_per_second": 200.0,
        "message_bytes": 503,
        "maximum_message_bytes": 65536,
        "thresholds": {"round_trip_p95_ms": 100.0},
        "checks": {
            "round_trip_p95_within_reference_target": True,
            "message_within_core_limit": True,
        },
        "environment": {
            "local_hostname": "robot-owner-workstation",
            "platform": "Linux",
            "python": "3.13",
            "transport": "TCP network; fresh mutual-TLS connection per message",
        },
        "passed": True,
    }
    server = {
        "profile": "ump.reference.tls-network-server/v1",
        "repository_revision": REVISION,
        "robot_id": "benchmark-server",
        "bind_host": "0.0.0.0",
        "bind_port": 7443,
        "expected_messages": 1064,
        "received_messages": 1064,
        "elapsed_seconds": 8.0,
        "errors": [],
        "passed": True,
        "environment": {
            "hostname": "robot-edge-server",
            "platform": "Linux",
            "python": "3.13",
        },
    }
    return client, server


def bundle(directory: Path) -> Path:
    client, server = reports()
    manifest = {
        "protocol": "ump.lan-evidence/v1",
        "repository_revision": REVISION,
        "run_id": "warehouse-lan-2026-08-20",
        "conducted_at_ms": 1_776_729_600_000,
        "network_description": "Two Ubuntu hosts on the deployment Ethernet LAN",
        "client_report": write_json(directory / "client.json", client),
        "server_report": write_json(directory / "server.json", server),
    }
    path = directory / "lan-evidence.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class LanEvidenceTests(unittest.TestCase):
    def test_validates_integrity_and_cross_host_consistency(self):
        with TemporaryDirectory() as name:
            report = validate_lan_evidence_bundle(bundle(Path(name)))

        self.assertTrue(report["valid"])
        self.assertEqual(report["client_hostname"], "robot-owner-workstation")
        self.assertEqual(report["server_hostname"], "robot-edge-server")
        self.assertEqual(report["samples"], 1000)
        self.assertEqual(report["repository_revision"], REVISION)
        self.assertEqual(report["evidence_artifacts_verified"], 2)

    def test_rejects_same_host_and_loopback_evidence(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = bundle(directory)
            document = json.loads(path.read_text())
            client, server = reports()
            server["environment"]["hostname"] = client["environment"][
                "local_hostname"
            ]
            document["server_report"] = write_json(directory / "server.json", server)
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(LanEvidenceValidationError, "distinct"):
                validate_lan_evidence_bundle(path)

            client, server = reports()
            client["remote_host"] = "127.0.0.1"
            document["client_report"] = write_json(directory / "client.json", client)
            document["server_report"] = write_json(directory / "server.json", server)
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(LanEvidenceValidationError, "loopback"):
                validate_lan_evidence_bundle(path)

    def test_rejects_tampering_and_mismatched_runs(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = bundle(directory)
            (directory / "client.json").write_text("{}")
            with self.assertRaisesRegex(LanEvidenceValidationError, "digest"):
                validate_lan_evidence_bundle(path)

            path = bundle(directory)
            document = json.loads(path.read_text())
            _, server = reports()
            server["received_messages"] = 1000
            document["server_report"] = write_json(directory / "server.json", server)
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(LanEvidenceValidationError, "message counts"):
                validate_lan_evidence_bundle(path)

    def test_rejects_report_from_a_different_repository_revision(self):
        with TemporaryDirectory() as name:
            directory = Path(name)
            path = bundle(directory)
            document = json.loads(path.read_text())
            client, _ = reports()
            client["repository_revision"] = "b" * 40
            document["client_report"] = write_json(
                directory / "client.json", client
            )
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(LanEvidenceValidationError, "revision"):
                validate_lan_evidence_bundle(path)

    def test_cli_and_packaged_schema(self):
        with TemporaryDirectory() as name:
            path = bundle(Path(name))
            output = StringIO()
            with redirect_stdout(output):
                status = lan_evidence_main(["validate", str(path)])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            (Path(name) / "client.json").write_text("{}")
            errors = StringIO()
            with redirect_stderr(errors):
                status = lan_evidence_main(["validate", str(path)])
            self.assertEqual(status, 2)
            self.assertIn("digest", errors.getvalue())

        public = json.loads(
            (ROOT / "schemas" / "ump-lan-evidence-v1.schema.json").read_text()
        )
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(lan_evidence_main(["schema"]), 0)
        self.assertEqual(public, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
