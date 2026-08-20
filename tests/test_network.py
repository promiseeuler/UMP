import json
import socket
import ssl
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.network import (
    DiscoveryAnnouncement,
    DeliveryAcknowledgementError,
    NetworkProtocolError,
    PeerEndpoint,
    PeerIdentityError,
    ReplayError,
    SqliteReplayProtector,
    TlsMessageClient,
    TlsNetworkBus,
    TlsMessageServer,
    certificate_sha256,
    create_client_context,
    create_server_context,
    decode_discovery,
    encode_discovery,
    load_network_config,
    receive_frame,
    send_discovery,
    send_frame,
)
from ump.cli import network_config_main
from ump.network_config import network_config_schema, validate_network_config
from ump.models import Mode, RobotManifest, RobotState, Safety, payload
from ump.delivery import SqliteOutbox
from ump.delivery import SqliteInbox
from ump.runtime import Registry
from ump.simulation import capability
from ump.transport import SAFETY_STREAM, make_envelope


def run_openssl(*arguments, directory):
    subprocess.run(
        ["openssl", *arguments],
        cwd=directory,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def create_leaf(directory: Path, ca_cert: Path, ca_key: Path, robot_id: str):
    key = directory / f"{robot_id}.key"
    request = directory / f"{robot_id}.csr"
    certificate = directory / f"{robot_id}.crt"
    extensions = directory / f"{robot_id}.ext"
    extensions.write_text(f"subjectAltName=URI:urn:ump:robot:{robot_id}\n")
    run_openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-subj",
        f"/CN={robot_id}",
        directory=directory,
    )
    run_openssl(
        "x509",
        "-req",
        "-in",
        str(request),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(certificate),
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(extensions),
        directory=directory,
    )
    return certificate, key


class FramingTests(unittest.TestCase):
    def test_frame_round_trip(self):
        sender, receiver = socket.socketpair()
        try:
            send_frame(sender, b"hello UMP")
            self.assertEqual(receive_frame(receiver), b"hello UMP")
        finally:
            sender.close()
            receiver.close()

    def test_oversized_frame_header_is_rejected_before_body_read(self):
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(struct.pack("!I", 65_537))
            with self.assertRaisesRegex(NetworkProtocolError, "frame size"):
                receive_frame(receiver)
        finally:
            sender.close()
            receiver.close()

    def test_partial_frame_is_rejected(self):
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(struct.pack("!I", 10) + b"short")
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(NetworkProtocolError, "inside a UMP frame"):
                receive_frame(receiver)
        finally:
            sender.close()
            receiver.close()


class DurableReplayTests(unittest.TestCase):
    def test_legacy_replay_database_migrates_without_reopening_old_sequence(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy-replay.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE replay_sessions (
                    peer_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(peer_id, session_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO replay_sessions VALUES (?, ?, ?, ?)",
                ("peer-1", "session-1", 7, 1_000),
            )
            connection.commit()
            connection.close()

            replay = SqliteReplayProtector(path)
            with self.assertRaises(ReplayError):
                replay.accept("peer-1", "session-1", 7)
            replay.accept("peer-1", "session-1", 1, SAFETY_STREAM)
            replay.close()

    def test_operational_and_safety_streams_have_independent_sequence_floors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            replay = SqliteReplayProtector(
                Path(temporary_directory) / "replay.sqlite3"
            )
            replay.accept("peer-1", "session-1", 1)
            replay.accept("peer-1", "session-1", 1, SAFETY_STREAM)
            with self.assertRaises(ReplayError):
                replay.accept("peer-1", "session-1", 1, SAFETY_STREAM)
            replay.close()

    def test_sequence_floor_survives_protector_restart(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "replay.sqlite3"
            first = SqliteReplayProtector(path)
            first.accept("robot-1", "session-1", 7)
            first.close()

            reopened = SqliteReplayProtector(path)
            with self.assertRaisesRegex(ReplayError, "replayed sequence"):
                reopened.accept("robot-1", "session-1", 7)
            with self.assertRaisesRegex(ReplayError, "replayed sequence"):
                reopened.accept("robot-1", "session-1", 6)
            reopened.accept("robot-1", "session-1", 8)
            reopened.close()


class DiscoveryTests(unittest.TestCase):
    def test_announcement_round_trip_and_expiry(self):
        announcement = DiscoveryAnnouncement(
            robot_id="robot-1",
            host="192.0.2.10",
            port=7443,
            certificate_sha256="a" * 64,
            expires_at_ms=2_000,
        )
        self.assertEqual(decode_discovery(encode_discovery(announcement), 1_000), announcement)
        with self.assertRaisesRegex(NetworkProtocolError, "expired"):
            decode_discovery(encode_discovery(announcement), 2_001)

    def test_udp_discovery_sender_transmits_only_the_hint(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1.0)
        try:
            port = receiver.getsockname()[1]
            announcement = DiscoveryAnnouncement(
                "robot-1", "127.0.0.1", 7443, "b" * 64, 2_000
            )
            send_discovery(announcement, "127.0.0.1", port)
            encoded, _ = receiver.recvfrom(2_049)
            self.assertEqual(decode_discovery(encoded, 1_000), announcement)
        finally:
            receiver.close()


class MutualTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary_directory.name)
        cls.ca_key = cls.directory / "ca.key"
        cls.ca_cert = cls.directory / "ca.crt"
        run_openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(cls.ca_key),
            "-out",
            str(cls.ca_cert),
            "-days",
            "1",
            "-sha256",
            "-subj",
            "/CN=UMP Test CA",
            directory=cls.directory,
        )
        cls.server_cert, cls.server_key = create_leaf(
            cls.directory, cls.ca_cert, cls.ca_key, "robot-server"
        )
        cls.client_cert, cls.client_key = create_leaf(
            cls.directory, cls.ca_cert, cls.ca_key, "robot-client"
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def setUp(self):
        self.received = []
        self.received_event = Event()
        context = create_server_context(self.server_cert, self.server_key, self.ca_cert)
        self.server = TlsMessageServer(
            "robot-server", "127.0.0.1", 0, context, self._receive
        )
        self.host, self.port = self.server.start()
        self.client = TlsMessageClient(
            "robot-client",
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
        )

    def tearDown(self):
        self.server.stop()

    def _receive(self, envelope):
        self.received.append(envelope)
        self.received_event.set()

    def envelope(self, source_id="robot-client"):
        return make_envelope(
            "state",
            source_id,
            "client-session",
            1,
            1_000,
            {
                "robot_id": source_id,
                "mode": "idle",
                "safety": "normal",
                "activity": "Waiting for work",
                "intent": "Observe peers",
                "progress": 0.0,
                "summary": f"{source_id} is idle.",
                "fresh_for_ms": 2_000,
                "blockers": [],
                "assignment_id": None,
            },
        )

    def test_mutual_tls_delivers_authenticated_envelope(self):
        envelope = self.envelope()
        self.client.send(
            self.host, self.port, "robot-server", envelope
        )
        self.assertTrue(self.received_event.wait(2.0))
        self.assertEqual(self.received, [envelope])
        self.assertEqual(self.server.errors, [])

    def test_tls_accepts_independent_operational_and_safety_sequences(self):
        operational = self.envelope()
        safety = make_envelope(
            "state",
            "robot-client",
            "client-session",
            1,
            1_001,
            {**operational.payload, "safety": "protective_stop"},
            stream=SAFETY_STREAM,
        )
        self.client.send(self.host, self.port, "robot-server", operational)
        self.client.send(self.host, self.port, "robot-server", safety)
        deadline = time.monotonic() + 2.0
        while len(self.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.received, [operational, safety])
        self.assertEqual(self.server.errors, [])

    def test_client_can_pin_server_certificate_from_discovery_hint(self):
        der = ssl.PEM_cert_to_DER_cert(self.server_cert.read_text())
        fingerprint = certificate_sha256(der)
        self.client.send(
            self.host,
            self.port,
            "robot-server",
            self.envelope(),
            fingerprint,
        )
        self.assertTrue(self.received_event.wait(2.0))

    def test_client_rejects_wrong_discovery_certificate_fingerprint(self):
        with self.assertRaisesRegex(PeerIdentityError, "fingerprint"):
            self.client.send(
                self.host,
                self.port,
                "robot-server",
                self.envelope(),
                "0" * 64,
            )

    def test_client_rejects_locally_revoked_server_certificate(self):
        fingerprint = certificate_sha256(
            ssl.PEM_cert_to_DER_cert(self.server_cert.read_text())
        )
        client = TlsMessageClient(
            "robot-client",
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
            certificate_revoked=lambda candidate: candidate == fingerprint,
        )
        with self.assertRaisesRegex(PeerIdentityError, "locally revoked"):
            client.send(self.host, self.port, "robot-server", self.envelope())

    def test_server_rejects_locally_revoked_client_certificate(self):
        self.server.stop()
        fingerprint = certificate_sha256(
            ssl.PEM_cert_to_DER_cert(self.client_cert.read_text())
        )
        self.server = TlsMessageServer(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            self._receive,
            certificate_revoked=lambda candidate: candidate == fingerprint,
        )
        self.host, self.port = self.server.start()
        with self.assertRaisesRegex(DeliveryAcknowledgementError, "without"):
            self.client.send(self.host, self.port, "robot-server", self.envelope())
        deadline = time.monotonic() + 2.0
        while not self.server.errors and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.received)
        self.assertTrue(
            any("locally revoked" in str(error) for error in self.server.errors)
        )

    def test_client_rejects_unexpected_server_robot_identity(self):
        with self.assertRaisesRegex(PeerIdentityError, "expected peer"):
            self.client.send(
                self.host, self.port, "different-server", self.envelope()
            )

    def test_client_rejects_source_different_from_local_identity(self):
        with self.assertRaisesRegex(PeerIdentityError, "local identity"):
            self.client.send(
                self.host,
                self.port,
                "robot-server",
                self.envelope("different-client"),
            )

    def test_server_rejects_envelope_spoofed_by_authenticated_client(self):
        spoofing_client = TlsMessageClient(
            "different-client",
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
        )
        with self.assertRaisesRegex(DeliveryAcknowledgementError, "without"):
            spoofing_client.send(
                self.host,
                self.port,
                "robot-server",
                self.envelope("different-client"),
            )
        deadline = time.monotonic() + 2.0
        while not self.server.errors and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.received)
        self.assertTrue(
            any(isinstance(error, PeerIdentityError) for error in self.server.errors)
        )

    def test_server_rejects_exact_envelope_replay(self):
        envelope = self.envelope()
        self.client.send(self.host, self.port, "robot-server", envelope)
        self.assertTrue(self.received_event.wait(2.0))
        with self.assertRaisesRegex(DeliveryAcknowledgementError, "without"):
            self.client.send(self.host, self.port, "robot-server", envelope)
        deadline = time.monotonic() + 2.0
        while not self.server.errors and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.received, [envelope])
        self.assertTrue(any(isinstance(error, ReplayError) for error in self.server.errors))

    def test_server_rejects_authenticated_replay_after_restart(self):
        self.server.stop()
        replay_path = self.directory / f"replay-{time.time_ns()}.sqlite3"
        first = TlsMessageServer(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            self._receive,
            replay_protector=SqliteReplayProtector(replay_path),
        )
        host, port = first.start()
        envelope = self.envelope()
        self.client.send(host, port, "robot-server", envelope)
        self.assertTrue(self.received_event.wait(2.0))
        first.stop()

        restarted = TlsMessageServer(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            self._receive,
            replay_protector=SqliteReplayProtector(replay_path),
        )
        host, port = restarted.start()
        with self.assertRaisesRegex(DeliveryAcknowledgementError, "without"):
            self.client.send(host, port, "robot-server", envelope)
        deadline = time.monotonic() + 2.0
        while not restarted.errors and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.received, [envelope])
        self.assertTrue(any(isinstance(error, ReplayError) for error in restarted.errors))
        restarted.stop()

    def test_durable_inbox_acknowledges_exact_retry_without_reprocessing(self):
        self.server.stop()
        suffix = time.time_ns()
        server = TlsMessageServer(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            self._receive,
            replay_protector=SqliteReplayProtector(
                self.directory / f"inbox-replay-{suffix}.sqlite3"
            ),
            inbox_path=self.directory / f"inbox-{suffix}.sqlite3",
        )
        host, port = server.start()
        envelope = self.envelope()
        first_ack = self.client.send(host, port, "robot-server", envelope)
        second_ack = self.client.send(host, port, "robot-server", envelope)
        self.assertEqual(first_ack.status, "received")
        self.assertEqual(second_ack.status, "duplicate")
        self.assertTrue(self.received_event.wait(2.0))
        time.sleep(0.05)
        self.assertEqual(self.received, [envelope])
        server.stop()

    def test_exact_retry_recovers_staged_inbox_after_replay_commit_crash_window(self):
        self.server.stop()
        suffix = time.time_ns()
        replay_path = self.directory / f"staged-replay-{suffix}.sqlite3"
        inbox_path = self.directory / f"staged-inbox-{suffix}.sqlite3"
        envelope = self.envelope()
        staged = SqliteInbox(inbox_path, lambda _: None, worker_count=1)
        staged.enqueue(envelope, 1_000, ready=False)
        staged.close()
        replay = SqliteReplayProtector(replay_path)
        replay.accept(envelope.source_id, envelope.session_id, envelope.sequence)
        replay.close()

        server = TlsMessageServer(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            self._receive,
            replay_protector=SqliteReplayProtector(replay_path),
            inbox_path=inbox_path,
        )
        host, port = server.start()
        acknowledgement = self.client.send(host, port, "robot-server", envelope)
        self.assertEqual(acknowledgement.status, "duplicate")
        self.assertTrue(self.received_event.wait(2.0))
        self.assertEqual(self.received, [envelope])
        server.stop()

    def test_server_certificate_has_stable_sha256_for_discovery_hint(self):
        pem = self.server_cert.read_text()
        der = ssl.PEM_cert_to_DER_cert(pem)
        fingerprint = certificate_sha256(der)
        self.assertEqual(len(fingerprint), 64)

    def test_file_configuration_resolves_credentials_and_peers(self):
        config_path = self.directory / "network.json"
        config_path.write_text(
            json.dumps(
                {
                    "robot_id": "robot-client",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "certificate_path": self.client_cert.name,
                    "private_key_path": self.client_key.name,
                    "ca_path": self.ca_cert.name,
                    "replay_database_path": "state/replay.sqlite3",
                    "inbox_database_path": "state/inbox.sqlite3",
                    "outbox_database_path": "state/outbox.sqlite3",
                    "maximum_pending_deliveries": 500,
                    "timeout": 1.5,
                    "peers": [
                        {
                            "robot_id": "robot-server",
                            "host": "127.0.0.1",
                            "port": 7443,
                            "certificate_sha256": "c" * 64,
                        }
                    ],
                }
            )
        )
        config = load_network_config(config_path)
        self.assertEqual(config.robot_id, "robot-client")
        self.assertEqual(config.certificate_path, self.client_cert)
        self.assertEqual(config.peers[0].robot_id, "robot-server")
        self.assertEqual(config.peers[0].allowed_message_types, ())
        self.assertEqual(
            config.replay_database_path,
            self.directory / "state" / "replay.sqlite3",
        )
        self.assertEqual(
            config.inbox_database_path,
            self.directory / "state" / "inbox.sqlite3",
        )
        self.assertEqual(config.maximum_pending_deliveries, 500)
        self.assertEqual(config.reserved_safety_deliveries, 64)

    def test_file_configuration_rejects_unknown_security_fields(self):
        base = {
            "robot_id": "robot-client",
            "bind_host": "127.0.0.1",
            "bind_port": 0,
            "certificate_path": self.client_cert.name,
            "private_key_path": self.client_key.name,
            "ca_path": self.ca_cert.name,
            "replay_database_path": "state/replay.sqlite3",
            "inbox_database_path": "state/inbox.sqlite3",
            "outbox_database_path": "state/outbox.sqlite3",
            "peers": [],
        }
        path = self.directory / "strict-network.json"
        document = dict(base, certificate_fingerprint="a" * 64)
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            load_network_config(path)

        document = dict(base)
        document["peers"] = [
            {
                "robot_id": "robot-server",
                "host": "127.0.0.1",
                "port": 7443,
                "allowed_message_type": ["state"],
            }
        ]
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            load_network_config(path)

    def test_file_configuration_rejects_weak_types_and_policy_values(self):
        base = {
            "robot_id": "robot-client",
            "bind_host": "127.0.0.1",
            "bind_port": 0,
            "certificate_path": self.client_cert.name,
            "private_key_path": self.client_key.name,
            "ca_path": self.ca_cert.name,
            "replay_database_path": "state/replay.sqlite3",
            "inbox_database_path": "state/inbox.sqlite3",
            "outbox_database_path": "state/outbox.sqlite3",
            "peers": [],
        }
        path = self.directory / "typed-network.json"
        for field, value in (
            ("bind_port", True),
            ("maximum_pending_deliveries", True),
            ("timeout", True),
        ):
            with self.subTest(field=field):
                path.write_text(json.dumps(dict(base, **{field: value})))
                with self.assertRaises(ValueError):
                    load_network_config(path)

        for peer in (
            {
                "robot_id": "robot-server",
                "host": "127.0.0.1",
                "port": True,
            },
            {
                "robot_id": "robot-server",
                "host": "127.0.0.1",
                "port": 7443,
                "certificate_sha256": "A" * 64,
            },
            {
                "robot_id": "robot-server",
                "host": "127.0.0.1",
                "port": 7443,
                "allowed_capabilities": ["unversioned"],
            },
        ):
            with self.subTest(peer=peer):
                document = dict(base, peers=[peer])
                path.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    load_network_config(path)

    def test_network_config_cli_emits_non_secret_summary_and_schema(self):
        path = self.directory / "network-cli.json"
        path.write_text(
            json.dumps(
                {
                    "robot_id": "robot-client",
                    "bind_host": "127.0.0.1",
                    "bind_port": 0,
                    "certificate_path": self.client_cert.name,
                    "private_key_path": self.client_key.name,
                    "ca_path": self.ca_cert.name,
                    "replay_database_path": "state/replay.sqlite3",
                    "inbox_database_path": "state/inbox.sqlite3",
                    "outbox_database_path": "state/outbox.sqlite3",
                    "peers": [],
                }
            )
        )
        with patch("builtins.print") as output:
            self.assertEqual(network_config_main(["validate", str(path)]), 0)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report, validate_network_config(path))
        self.assertNotIn("private_key", json.dumps(report))
        self.assertEqual(
            network_config_schema(),
            json.loads(
                (Path(__file__).parents[1] / "schemas" / "ump-network-config-v1.schema.json").read_text()
            ),
        )

    def test_network_bus_delivers_manifest_and_state_to_remote_registry(self):
        server_bus = TlsNetworkBus(
            "robot-server",
            "127.0.0.1",
            0,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            create_client_context(self.server_cert, self.server_key, self.ca_cert),
        )
        client_bus = TlsNetworkBus(
            "robot-client",
            "127.0.0.1",
            0,
            create_server_context(self.client_cert, self.client_key, self.ca_cert),
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
        )
        try:
            server_host, server_port = server_bus.start()
            client_bus.start()
            fingerprint = certificate_sha256(
                ssl.PEM_cert_to_DER_cert(self.server_cert.read_text())
            )
            client_bus.add_peer(
                PeerEndpoint(
                    "robot-server",
                    server_host,
                    server_port,
                    fingerprint,
                    ("manifest", "state"),
                    ("ump.navigation.inspect/v1",),
                )
            )
            registry = Registry(server_bus)
            manifest = RobotManifest(
                "robot-client",
                "Example Robotics",
                "C1",
                "mobile_base",
                (capability("ump.navigation.inspect/v1", "Inspect a route"),),
            )
            state = RobotState(
                "robot-client",
                Mode.IDLE,
                Safety.NORMAL,
                "Waiting for work",
                "Observe remote peers",
                0.0,
                "robot-client is idle and available.",
            )
            client_bus.publish(
                make_envelope(
                    "manifest",
                    "robot-client",
                    "network-session",
                    1,
                    1_000,
                    payload(manifest),
                )
            )
            client_bus.publish(
                make_envelope(
                    "state",
                    "robot-client",
                    "network-session",
                    2,
                    1_001,
                    payload(state),
                )
            )
            deadline = time.monotonic() + 2.0
            while (
                registry.peers.get("robot-client", None) is None
                or registry.peers["robot-client"].state is None
            ) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(registry.peers["robot-client"].state, state)
            self.assertEqual(server_bus.errors, ())
            self.assertEqual(client_bus.errors, ())
        finally:
            client_bus.stop()
            server_bus.stop()

    def test_outbox_delivers_in_order_after_sender_restart_and_peer_recovery(self):
        suffix = time.time_ns()
        outbox_path = self.directory / f"outbox-{suffix}.sqlite3"
        unavailable = socket.socket()
        unavailable.bind(("127.0.0.1", 0))
        remote_port = unavailable.getsockname()[1]
        unavailable.close()
        peer = PeerEndpoint(
            "robot-server",
            "127.0.0.1",
            remote_port,
            allowed_message_types=("manifest", "state"),
            allowed_capabilities=("ump.navigation.inspect/v1",),
        )

        sender = TlsNetworkBus(
            "robot-client",
            "127.0.0.1",
            0,
            create_server_context(self.client_cert, self.client_key, self.ca_cert),
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
            replay_protector=SqliteReplayProtector(
                self.directory / f"sender-replay-{suffix}.sqlite3"
            ),
            inbox_path=self.directory / f"sender-inbox-{suffix}.sqlite3",
            outbox=SqliteOutbox(outbox_path),
        )
        sender.add_peer(peer)
        sender.start()
        manifest = RobotManifest(
            "robot-client",
            "Example Robotics",
            "C1",
            "mobile_base",
            (capability("ump.navigation.inspect/v1", "Inspect a route"),),
        )
        state = RobotState(
            "robot-client",
            Mode.IDLE,
            Safety.NORMAL,
            "Waiting for work",
            "Observe remote peers",
            0.0,
            "robot-client is idle and available.",
        )
        sender.publish(
            make_envelope(
                "manifest",
                "robot-client",
                "durable-session",
                1,
                1_000,
                payload(manifest),
            )
        )
        sender.publish(
            make_envelope(
                "state",
                "robot-client",
                "durable-session",
                2,
                1_001,
                payload(state),
            )
        )
        self.assertEqual(sender.delivery_metrics.pending, 2)
        sender.stop()

        receiver = TlsNetworkBus(
            "robot-server",
            "127.0.0.1",
            remote_port,
            create_server_context(self.server_cert, self.server_key, self.ca_cert),
            create_client_context(self.server_cert, self.server_key, self.ca_cert),
            replay_protector=SqliteReplayProtector(
                self.directory / f"receiver-replay-{suffix}.sqlite3"
            ),
            inbox_path=self.directory / f"receiver-inbox-{suffix}.sqlite3",
        )
        registry = Registry(receiver)
        receiver.start()
        restarted = TlsNetworkBus(
            "robot-client",
            "127.0.0.1",
            0,
            create_server_context(self.client_cert, self.client_key, self.ca_cert),
            create_client_context(self.client_cert, self.client_key, self.ca_cert),
            replay_protector=SqliteReplayProtector(
                self.directory / f"sender-replay-{suffix}.sqlite3"
            ),
            inbox_path=self.directory / f"sender-inbox-{suffix}.sqlite3",
            outbox=SqliteOutbox(outbox_path),
        )
        restarted.add_peer(peer)
        restarted.start()
        try:
            deadline = time.monotonic() + 3.0
            while (
                registry.peers.get("robot-client") is None
                or registry.peers["robot-client"].state is None
            ) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(registry.peers["robot-client"].state, state)
            self.assertEqual(restarted.delivery_metrics.pending, 0)
            self.assertEqual(restarted.delivery_metrics.delivered, 2)
        finally:
            restarted.stop()
            receiver.stop()

    def test_network_disclosure_is_deny_by_default_and_filters_capabilities(self):
        public = capability("ump.navigation.inspect/v1", "Inspect a route")
        private = capability("vendor.private.admin/v1", "Private service action")
        envelope = make_envelope(
            "manifest",
            "robot-client",
            "session-1",
            1,
            1_000,
            payload(
                RobotManifest(
                    "robot-client",
                    "Example Robotics",
                    "C1",
                    "mobile_base",
                    (public, private),
                )
            ),
        )
        denied = PeerEndpoint("robot-server", "127.0.0.1", 7443)
        self.assertIsNone(TlsNetworkBus._disclose(denied, envelope))

        permitted = PeerEndpoint(
            "robot-server",
            "127.0.0.1",
            7443,
            allowed_message_types=("manifest",),
            allowed_capabilities=(public.name,),
        )
        disclosed = TlsNetworkBus._disclose(permitted, envelope)
        assert disclosed is not None
        self.assertEqual(
            [item["name"] for item in disclosed.payload["capabilities"]],
            [public.name],
        )
        self.assertEqual(len(envelope.payload["capabilities"]), 2)


if __name__ == "__main__":
    unittest.main()
