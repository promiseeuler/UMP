from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from ump.cli import deployment_main
from ump.credentials import read_active_credential
from ump.deployment import (
    DeploymentError,
    default_local_topology,
    deployment_topology_schema,
    generate_deployment_bundle,
    verify_local_awareness,
)
from ump.network import load_network_config


ROOT = Path(__file__).resolve().parents[1]


def available_ports(count: int) -> list[int]:
    listeners = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


def topology(count: int = 3) -> dict:
    document = default_local_topology(count, 17443)
    for node, port in zip(document["nodes"], available_ports(count), strict=True):
        node["port"] = port
    return document


class DeploymentTests(unittest.TestCase):
    def test_generator_creates_valid_isolated_read_only_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            bundle = generate_deployment_bundle(
                topology(), root, development_pki=True
            )
            self.assertFalse(bundle["security"]["production_eligible"])
            self.assertEqual(len(bundle["nodes"]), 3)
            for node in bundle["nodes"]:
                network_path = root / node["network"]
                config = load_network_config(network_path)
                self.assertEqual(len(config.peers), 2)
                self.assertTrue(all(peer.certificate_sha256 for peer in config.peers))
                self.assertTrue(all(peer.allowed_message_types == ("manifest", "state") for peer in config.peers))
                self.assertEqual(config.private_key_path.stat().st_mode & 0o777, 0o600)
                credential = read_active_credential(
                    network_path.parent / "state" / "credentials.sqlite3",
                    node["robot_id"],
                    config.certificate_path,
                    config.private_key_path,
                    config.ca_path,
                )
                self.assertEqual(credential.robot_id, node["robot_id"])
                self.assertTrue((root / node["launch"]).stat().st_mode & 0o100)
                self.assertTrue((root / node["preflight"]).stat().st_mode & 0o100)

    def test_generated_local_network_observes_every_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            generate_deployment_bundle(topology(), root, development_pki=True)
            report = verify_local_awareness(root)
            self.assertTrue(report["passed"])
            self.assertFalse(report["production_qualification"])
            self.assertEqual(report["robot_count"], 3)
            self.assertTrue(all(report["checks"].values()))
            self.assertTrue((root / "local-awareness-report.json").is_file())

    def test_generation_requires_explicit_development_pki_and_empty_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lab"
            with self.assertRaisesRegex(DeploymentError, "development-pki"):
                generate_deployment_bundle(topology(), root)
            root.mkdir()
            (root / "existing.txt").write_text("owner data", encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "empty"):
                generate_deployment_bundle(topology(), root, development_pki=True)

    def test_local_topology_rejects_non_loopback_hosts(self):
        document = topology(2)
        document["nodes"][0]["host"] = "192.0.2.10"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DeploymentError, "loopback"):
                generate_deployment_bundle(
                    document, Path(directory) / "lab", development_pki=True
                )

    def test_quickstart_and_wizard_cli_generate_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "quick"
            ports = available_ports(2)
            with redirect_stdout(io.StringIO()) as stdout:
                status = deployment_main(
                    [
                        "quickstart",
                        "--output",
                        str(first),
                        "--robots",
                        "2",
                        "--base-port",
                        str(min(ports)),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout.getvalue())["profile"], "ump.deployment-bundle/v1")

            second = Path(directory) / "wizard"
            answers = ["2", "robot-a", "Maker A", "A1", "mobile_base", "robot-b", "Maker B", "B1", "arm"]
            with (
                patch("builtins.input", side_effect=answers),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                status = deployment_main(
                    ["wizard", "--output", str(second), "--base-port", "19443"]
                )
            self.assertEqual(status, 0)
            bundle = json.loads((second / "bundle.json").read_text())
            self.assertEqual([node["robot_id"] for node in bundle["nodes"]], ["robot-a", "robot-b"])

    def test_packaged_topology_schema_has_expected_profile(self):
        schema = deployment_topology_schema()
        self.assertEqual(
            schema["properties"]["profile"]["const"],
            "ump.deployment-topology/v1",
        )
        public = json.loads(
            (ROOT / "schemas" / "ump-deployment-topology-v1.schema.json").read_text()
        )
        self.assertEqual(public, schema)


if __name__ == "__main__":
    unittest.main()
