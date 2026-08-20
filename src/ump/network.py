from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import socket
import ssl
import sqlite3
import struct
from threading import Event, Lock, RLock, Thread
import time
from typing import Any

from .transport import (
    MAX_MESSAGE_BYTES,
    OPERATIONAL_STREAM,
    SAFETY_STREAM,
    Envelope,
    Handler,
    InMemoryBus,
    MESSAGE_TYPES,
    decode_envelope,
    encode_envelope,
)
from .delivery import DeliveryMetrics, InboxFailure, SqliteInbox, SqliteOutbox

IDENTITY_URI_PREFIX = "urn:ump:robot:"
DISCOVERY_PROTOCOL = "ump-discovery/0.1"
MAX_DISCOVERY_BYTES = 2_048
TLS_ALPN = "ump/0.1"
DELIVERY_ACK_PROTOCOL = "ump-delivery-ack/0.1"


class NetworkProtocolError(ValueError):
    pass


class PeerIdentityError(NetworkProtocolError):
    pass


class ReplayError(NetworkProtocolError):
    pass


class DeliveryAcknowledgementError(NetworkProtocolError):
    pass


@dataclass(frozen=True)
class DeliveryAcknowledgement:
    message_id: str
    status: str


def encode_delivery_ack(acknowledgement: DeliveryAcknowledgement) -> bytes:
    if acknowledgement.status not in {"received", "processed", "duplicate"}:
        raise DeliveryAcknowledgementError("invalid delivery acknowledgement status")
    return json.dumps(
        {
            "protocol": DELIVERY_ACK_PROTOCOL,
            "message_id": acknowledgement.message_id,
            "status": acknowledgement.status,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_delivery_ack(encoded: bytes, expected_message_id: str) -> DeliveryAcknowledgement:
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeliveryAcknowledgementError(
            "delivery acknowledgement is not valid UTF-8 JSON"
        ) from error
    if not isinstance(value, dict) or value.get("protocol") != DELIVERY_ACK_PROTOCOL:
        raise DeliveryAcknowledgementError("invalid delivery acknowledgement protocol")
    if value.get("message_id") != expected_message_id:
        raise DeliveryAcknowledgementError("delivery acknowledgement message ID mismatch")
    status = value.get("status")
    if status not in {"received", "processed", "duplicate"}:
        raise DeliveryAcknowledgementError("invalid delivery acknowledgement status")
    return DeliveryAcknowledgement(expected_message_id, status)


class ReplayProtector:
    def __init__(self, maximum_sessions: int = 4_096) -> None:
        if maximum_sessions < 1:
            raise ValueError("maximum_sessions must be positive")
        self.maximum_sessions = maximum_sessions
        self._sequences: OrderedDict[tuple[str, str, str], int] = OrderedDict()
        self._lock = Lock()

    def accept(
        self,
        peer_id: str,
        session_id: str,
        sequence: int,
        stream: str = OPERATIONAL_STREAM,
    ) -> None:
        key = (peer_id, session_id, stream)
        with self._lock:
            previous = self._sequences.get(key, 0)
            if sequence <= previous:
                raise ReplayError(
                    f"replayed sequence {sequence} for {peer_id} session {session_id} stream {stream}"
                )
            self._sequences[key] = sequence
            self._sequences.move_to_end(key)
            while len(self._sequences) > self.maximum_sessions:
                self._sequences.popitem(last=False)

    def close(self) -> None:
        pass


class SqliteReplayProtector:
    """Durable monotonic sequence enforcement for authenticated peer sessions."""

    def __init__(self, path: str | Path) -> None:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_sessions (
                peer_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                last_sequence INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY(peer_id, session_id, stream)
            )
            """
        )
        self._migrate_replay_streams()

    def _migrate_replay_streams(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(replay_sessions)")
        }
        if "stream" in columns:
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE replay_sessions RENAME TO replay_sessions_legacy;
            CREATE TABLE replay_sessions (
                peer_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                last_sequence INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY(peer_id, session_id, stream)
            );
            INSERT INTO replay_sessions
            (peer_id, session_id, stream, last_sequence, updated_at_ms)
            SELECT peer_id, session_id, 'operational', last_sequence, updated_at_ms
            FROM replay_sessions_legacy;
            DROP TABLE replay_sessions_legacy;
            COMMIT;
            """
        )

    def accept(
        self,
        peer_id: str,
        session_id: str,
        sequence: int,
        stream: str = OPERATIONAL_STREAM,
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT last_sequence FROM replay_sessions
                    WHERE peer_id = ? AND session_id = ? AND stream = ?
                    """,
                    (peer_id, session_id, stream),
                ).fetchone()
                previous = row[0] if row else 0
                if sequence <= previous:
                    raise ReplayError(
                        f"replayed sequence {sequence} for {peer_id} session {session_id} stream {stream}"
                    )
                self._connection.execute(
                    """
                    INSERT INTO replay_sessions
                    (peer_id, session_id, stream, last_sequence, updated_at_ms)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(peer_id, session_id, stream) DO UPDATE SET
                        last_sequence = excluded.last_sequence,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        peer_id,
                        session_id,
                        stream,
                        sequence,
                        int(time.time() * 1_000),
                    ),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def certificate_robot_id(certificate: dict[str, Any]) -> str:
    identities = {
        value.removeprefix(IDENTITY_URI_PREFIX)
        for kind, value in certificate.get("subjectAltName", ())
        if kind == "URI" and value.startswith(IDENTITY_URI_PREFIX)
    }
    if len(identities) != 1 or not next(iter(identities), ""):
        raise PeerIdentityError("certificate must contain exactly one UMP robot URI identity")
    return next(iter(identities))


def certificate_sha256(certificate_der: bytes) -> str:
    if not certificate_der:
        raise PeerIdentityError("peer did not provide a certificate")
    return hashlib.sha256(certificate_der).hexdigest()


def create_server_context(
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(str(certificate_path), str(private_key_path))
    context.load_verify_locations(cafile=str(ca_path))
    context.set_alpn_protocols([TLS_ALPN])
    return context


def create_client_context(
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(str(certificate_path), str(private_key_path))
    context.load_verify_locations(cafile=str(ca_path))
    context.set_alpn_protocols([TLS_ALPN])
    return context


def send_frame(connection: socket.socket, encoded: bytes) -> None:
    if not encoded or len(encoded) > MAX_MESSAGE_BYTES:
        raise NetworkProtocolError("frame size is outside the UMP core profile")
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def receive_frame(connection: socket.socket) -> bytes | None:
    header = _receive_exact(connection, 4, allow_clean_eof=True)
    if header is None:
        return None
    (size,) = struct.unpack("!I", header)
    if not 1 <= size <= MAX_MESSAGE_BYTES:
        raise NetworkProtocolError("frame size is outside the UMP core profile")
    body = _receive_exact(connection, size, allow_clean_eof=False)
    assert body is not None
    return body


def _receive_exact(
    connection: socket.socket, size: int, *, allow_clean_eof: bool
) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            if not chunks and allow_clean_eof:
                return None
            raise NetworkProtocolError("connection closed inside a UMP frame")
        chunks.extend(chunk)
    return bytes(chunks)


class TlsMessageServer:
    def __init__(
        self,
        robot_id: str,
        host: str,
        port: int,
        context: ssl.SSLContext,
        handler: Callable[[Envelope], None],
        *,
        connection_timeout: float = 2.0,
        replay_protector: ReplayProtector | SqliteReplayProtector | None = None,
        inbox_path: str | Path | None = None,
        inbox_worker_count: int = 4,
        certificate_revoked: Callable[[str], bool] | None = None,
    ) -> None:
        if not robot_id:
            raise ValueError("robot_id cannot be empty")
        self.robot_id = robot_id
        self.host = host
        self.port = port
        self.context = context
        self.handler = handler
        self.connection_timeout = connection_timeout
        self.errors: list[Exception] = []
        self._errors_lock = Lock()
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._connection_threads: list[Thread] = []
        self._replay = replay_protector or ReplayProtector()
        self._certificate_revoked = certificate_revoked or (lambda _fingerprint: False)
        self._replay_closed = False
        self._inbox = (
            SqliteInbox(inbox_path, handler, inbox_worker_count)
            if inbox_path is not None
            else None
        )

    def start(self) -> tuple[str, int]:
        if self._socket is not None:
            raise RuntimeError("TLS message server is already running")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen()
        listener.settimeout(0.2)
        self._socket = listener
        self.host, self.port = listener.getsockname()[:2]
        self._thread = Thread(target=self._accept_loop, name="ump-tls-listener", daemon=True)
        self._thread.start()
        return self.host, self.port

    def _accept_loop(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self._record_error(error)
                return
            thread = Thread(
                target=self._handle_connection,
                args=(connection,),
                name="ump-tls-peer",
                daemon=True,
            )
            self._connection_threads.append(thread)
            thread.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(self.connection_timeout)
            with connection, self.context.wrap_socket(connection, server_side=True) as secure:
                if secure.selected_alpn_protocol() != TLS_ALPN:
                    raise NetworkProtocolError("TLS peer did not negotiate the UMP ALPN")
                peer_id = certificate_robot_id(secure.getpeercert())
                peer_fingerprint = certificate_sha256(
                    secure.getpeercert(binary_form=True)
                )
                if self._certificate_revoked(peer_fingerprint):
                    raise PeerIdentityError("peer certificate is locally revoked")
                encoded = receive_frame(secure)
                if encoded is None:
                    raise NetworkProtocolError("TLS connection contained no UMP frame")
                envelope = decode_envelope(encoded)
                if envelope.source_id != peer_id:
                    raise PeerIdentityError(
                        "envelope source does not match authenticated certificate identity"
                    )
                inbox_is_new = False
                if self._inbox is not None:
                    inbox_is_new = self._inbox.enqueue(
                        envelope, int(time.time() * 1_000), ready=False
                    )
                try:
                    self._replay.accept(
                        peer_id,
                        envelope.session_id,
                        envelope.sequence,
                        envelope.stream,
                    )
                except ReplayError:
                    if (
                        self._inbox is not None
                        and not inbox_is_new
                        and self._inbox.contains_exact(envelope)
                    ):
                        self._inbox.activate(envelope.message_id)
                        send_frame(
                            secure,
                            encode_delivery_ack(
                                DeliveryAcknowledgement(envelope.message_id, "duplicate")
                            ),
                        )
                        return
                    if self._inbox is not None and inbox_is_new:
                        self._inbox.discard_staged(envelope.message_id)
                    raise
                if self._inbox is not None:
                    self._inbox.activate(envelope.message_id)
                    status = "received" if inbox_is_new else "duplicate"
                else:
                    self.handler(envelope)
                    status = "processed"
                send_frame(
                    secure,
                    encode_delivery_ack(
                        DeliveryAcknowledgement(envelope.message_id, status)
                    ),
                )
        except (OSError, ssl.SSLError, NetworkProtocolError, ValueError) as error:
            self._record_error(error)

    def _record_error(self, error: Exception) -> None:
        with self._errors_lock:
            self.errors.append(error)
            del self.errors[:-1_000]

    @property
    def inbox_counts(self) -> dict[str, int]:
        return self._inbox.counts() if self._inbox is not None else {}

    def inbox_failures(self, limit: int = 100) -> tuple[InboxFailure, ...]:
        return self._inbox.failures(limit) if self._inbox is not None else ()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for thread in self._connection_threads:
            thread.join()
        self._connection_threads.clear()
        if not self._replay_closed:
            self._replay.close()
            self._replay_closed = True
        if self._inbox is not None:
            self._inbox.close()
            self._inbox = None


class TlsMessageClient:
    def __init__(
        self,
        robot_id: str,
        context: ssl.SSLContext,
        *,
        timeout: float = 2.0,
        certificate_revoked: Callable[[str], bool] | None = None,
    ) -> None:
        if not robot_id:
            raise ValueError("robot_id cannot be empty")
        self.robot_id = robot_id
        self.context = context
        self.timeout = timeout
        self._certificate_revoked = certificate_revoked or (lambda _fingerprint: False)

    def send(
        self,
        host: str,
        port: int,
        expected_robot_id: str,
        envelope: Envelope,
        expected_certificate_sha256: str | None = None,
    ) -> DeliveryAcknowledgement:
        if envelope.source_id != self.robot_id:
            raise PeerIdentityError("outbound envelope source does not match local identity")
        encoded = encode_envelope(envelope)
        with socket.create_connection((host, port), timeout=self.timeout) as connection:
            connection.settimeout(self.timeout)
            with self.context.wrap_socket(connection, server_hostname=None) as secure:
                if secure.selected_alpn_protocol() != TLS_ALPN:
                    raise NetworkProtocolError("TLS peer did not negotiate the UMP ALPN")
                actual_robot_id = certificate_robot_id(secure.getpeercert())
                if actual_robot_id != expected_robot_id:
                    raise PeerIdentityError(
                        f"expected peer {expected_robot_id}, authenticated {actual_robot_id}"
                    )
                actual_fingerprint = certificate_sha256(
                    secure.getpeercert(binary_form=True)
                )
                if self._certificate_revoked(actual_fingerprint):
                    raise PeerIdentityError("peer certificate is locally revoked")
                if expected_certificate_sha256 is not None:
                    if actual_fingerprint != expected_certificate_sha256.lower():
                        raise PeerIdentityError("peer certificate fingerprint does not match discovery hint")
                send_frame(secure, encoded)
                try:
                    acknowledgement = receive_frame(secure)
                except OSError as error:
                    raise DeliveryAcknowledgementError(
                        "peer closed without a delivery acknowledgement"
                    ) from error
                if acknowledgement is None:
                    raise DeliveryAcknowledgementError(
                        "peer closed without a delivery acknowledgement"
                    )
                return decode_delivery_ack(acknowledgement, envelope.message_id)


@dataclass(frozen=True)
class PeerEndpoint:
    robot_id: str
    host: str
    port: int
    certificate_sha256: str | None = None
    allowed_message_types: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.robot_id or not self.host or not 1 <= self.port <= 65_535:
            raise ValueError("invalid UMP peer endpoint")
        if self.certificate_sha256 is not None:
            fingerprint = self.certificate_sha256.lower()
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise ValueError("peer certificate fingerprint must be lowercase SHA-256")
        if len(self.allowed_message_types) != len(set(self.allowed_message_types)):
            raise ValueError("peer allowed message types must be unique")
        unknown_types = set(self.allowed_message_types) - MESSAGE_TYPES
        if unknown_types:
            raise ValueError(f"peer policy contains unknown message types: {sorted(unknown_types)}")
        if len(self.allowed_capabilities) != len(set(self.allowed_capabilities)):
            raise ValueError("peer allowed capabilities must be unique")


@dataclass(frozen=True)
class NetworkConfig:
    robot_id: str
    bind_host: str
    bind_port: int
    certificate_path: Path
    private_key_path: Path
    ca_path: Path
    replay_database_path: Path
    inbox_database_path: Path
    outbox_database_path: Path
    peers: tuple[PeerEndpoint, ...] = ()
    timeout: float = 2.0
    maximum_pending_deliveries: int = 10_000
    reserved_safety_deliveries: int = 64

    def __post_init__(self) -> None:
        if not self.robot_id or not self.bind_host:
            raise ValueError("network robot_id and bind_host are required")
        if not 0 <= self.bind_port <= 65_535:
            raise ValueError("network bind_port is invalid")
        if not 0.1 <= self.timeout <= 60.0:
            raise ValueError("network timeout must be between 0.1 and 60 seconds")
        if not 1 <= self.maximum_pending_deliveries <= 1_000_000:
            raise ValueError("maximum pending deliveries is outside supported bounds")
        if not 0 <= self.reserved_safety_deliveries < self.maximum_pending_deliveries:
            raise ValueError("reserved safety deliveries must fit inside the outbox")
        for path in (self.certificate_path, self.private_key_path, self.ca_path):
            if not path.is_file():
                raise ValueError(f"network credential file does not exist: {path}")
        peer_ids = [peer.robot_id for peer in self.peers]
        if len(peer_ids) != len(set(peer_ids)):
            raise ValueError("network peer robot IDs must be unique")


def load_network_config(path: str | Path) -> NetworkConfig:
    config_path = Path(path)
    try:
        value = json.loads(config_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("network configuration is not readable JSON") from error
    if not isinstance(value, dict):
        raise ValueError("network configuration must be an object")
    required = {
        "robot_id", "bind_host", "bind_port", "certificate_path",
        "private_key_path", "ca_path", "replay_database_path",
        "inbox_database_path", "outbox_database_path",
    }
    if missing := required - value.keys():
        raise ValueError(f"network configuration is missing {sorted(missing)}")

    def credential_path(field_name: str) -> Path:
        candidate = Path(value[field_name])
        return candidate if candidate.is_absolute() else config_path.parent / candidate

    try:
        peers = tuple(
            PeerEndpoint(
                robot_id=item["robot_id"],
                host=item["host"],
                port=item["port"],
                certificate_sha256=item.get("certificate_sha256"),
                allowed_message_types=tuple(item.get("allowed_message_types", ())),
                allowed_capabilities=tuple(item.get("allowed_capabilities", ())),
            )
            for item in value.get("peers", ())
        )
        return NetworkConfig(
            robot_id=value["robot_id"],
            bind_host=value["bind_host"],
            bind_port=value["bind_port"],
            certificate_path=credential_path("certificate_path"),
            private_key_path=credential_path("private_key_path"),
            ca_path=credential_path("ca_path"),
            replay_database_path=credential_path("replay_database_path"),
            inbox_database_path=credential_path("inbox_database_path"),
            outbox_database_path=credential_path("outbox_database_path"),
            peers=peers,
            timeout=value.get("timeout", 2.0),
            maximum_pending_deliveries=value.get("maximum_pending_deliveries", 10_000),
            reserved_safety_deliveries=value.get("reserved_safety_deliveries", 64),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("network configuration contains invalid values") from error


class TlsNetworkBus:
    """Message bus that bridges local subscribers to configured mutual-TLS peers.

    Peer endpoints are routing hints. Certificate-chain, robot identity, and
    optional fingerprint validation occur on every outbound connection.
    """

    def __init__(
        self,
        robot_id: str,
        host: str,
        port: int,
        server_context: ssl.SSLContext,
        client_context: ssl.SSLContext,
        *,
        timeout: float = 2.0,
        replay_protector: ReplayProtector | SqliteReplayProtector | None = None,
        inbox_path: str | Path | None = None,
        outbox: SqliteOutbox | None = None,
        certificate_revoked: Callable[[str], bool] | None = None,
    ) -> None:
        self.robot_id = robot_id
        self._local = InMemoryBus()
        self._peers: dict[str, PeerEndpoint] = {}
        self._peers_lock = Lock()
        self._send_locks = {
            OPERATIONAL_STREAM: Lock(),
            SAFETY_STREAM: Lock(),
        }
        self.delivery_errors: list[tuple[PeerEndpoint, Exception]] = []
        self._errors_lock = Lock()
        self._client = TlsMessageClient(
            robot_id,
            client_context,
            timeout=timeout,
            certificate_revoked=certificate_revoked,
        )
        self._outbox = outbox
        self._retry_stop = Event()
        self._retry_thread: Thread | None = None
        self._server = TlsMessageServer(
            robot_id,
            host,
            port,
            server_context,
            self._receive_authenticated,
            connection_timeout=timeout,
            replay_protector=replay_protector,
            inbox_path=inbox_path,
            certificate_revoked=certificate_revoked,
        )

    @classmethod
    def from_config(
        cls,
        config: NetworkConfig,
        *,
        certificate_revoked: Callable[[str], bool] | None = None,
    ) -> TlsNetworkBus:
        bus = cls(
            config.robot_id,
            config.bind_host,
            config.bind_port,
            create_server_context(
                config.certificate_path, config.private_key_path, config.ca_path
            ),
            create_client_context(
                config.certificate_path, config.private_key_path, config.ca_path
            ),
            timeout=config.timeout,
            replay_protector=SqliteReplayProtector(config.replay_database_path),
            inbox_path=config.inbox_database_path,
            outbox=SqliteOutbox(
                config.outbox_database_path,
                config.maximum_pending_deliveries,
                config.reserved_safety_deliveries,
            ),
            certificate_revoked=certificate_revoked,
        )
        for peer in config.peers:
            bus.add_peer(peer)
        return bus

    def add_discovery_hint(
        self,
        announcement: DiscoveryAnnouncement,
        *,
        observed_host: str | None = None,
    ) -> None:
        self.add_peer(
            PeerEndpoint(
                announcement.robot_id,
                observed_host or announcement.host,
                announcement.port,
                announcement.certificate_sha256,
            )
        )

    @property
    def trace(self) -> list[Envelope]:
        return self._local.trace

    @property
    def errors(self) -> tuple[Exception, ...]:
        with self._errors_lock:
            delivery = tuple(error for _, error in self.delivery_errors)
        return tuple(self._server.errors) + delivery

    def start(self) -> tuple[str, int]:
        endpoint = self._server.start()
        if self._outbox is not None and self._retry_thread is None:
            self._retry_stop.clear()
            self._retry_thread = Thread(
                target=self._retry_loop, name="ump-outbox", daemon=True
            )
            self._retry_thread.start()
        return endpoint

    def stop(self) -> None:
        self._retry_stop.set()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=2.0)
            self._retry_thread = None
        self._server.stop()
        if self._outbox is not None:
            self._outbox.close()
            self._outbox = None

    def subscribe(self, message_type: str, handler: Handler) -> None:
        self._local.subscribe(message_type, handler)

    def add_peer(self, endpoint: PeerEndpoint) -> None:
        if endpoint.robot_id == self.robot_id:
            raise ValueError("cannot add the local robot as a network peer")
        with self._peers_lock:
            self._peers[endpoint.robot_id] = endpoint

    def remove_peer(self, robot_id: str) -> None:
        with self._peers_lock:
            self._peers.pop(robot_id, None)

    def publish(self, envelope: Envelope) -> None:
        if envelope.source_id != self.robot_id:
            raise PeerIdentityError("local bus may publish only the configured robot identity")
        with self._peers_lock:
            peers = tuple(self._peers.values())
        disclosures = tuple(
            (peer, disclosed)
            for peer in peers
            if (disclosed := self._disclose(peer, envelope)) is not None
        )
        if self._outbox is not None:
            self._outbox.enqueue_many(
                ((peer.robot_id, disclosed) for peer, disclosed in disclosures),
                int(time.time() * 1_000),
            )
        self._local.publish(envelope)
        if self._outbox is not None:
            self.flush(stream=envelope.stream)
            return
        with self._send_locks[envelope.stream]:
            for peer, disclosed in disclosures:
                try:
                    self._client.send(
                        peer.host,
                        peer.port,
                        peer.robot_id,
                        disclosed,
                        peer.certificate_sha256,
                    )
                except (OSError, ssl.SSLError, NetworkProtocolError) as error:
                    with self._errors_lock:
                        self.delivery_errors.append((peer, error))
                        del self.delivery_errors[:-1_000]

    @property
    def delivery_metrics(self) -> DeliveryMetrics:
        if self._outbox is None:
            return DeliveryMetrics(0, 0, len(self.delivery_errors))
        return self._outbox.metrics()

    @property
    def inbox_counts(self) -> dict[str, int]:
        return self._server.inbox_counts

    def inbox_failures(self, limit: int = 100) -> tuple[InboxFailure, ...]:
        return self._server.inbox_failures(limit)

    def flush(
        self,
        now_ms: int | None = None,
        limit: int = 256,
        *,
        stream: str | None = None,
    ) -> int:
        if self._outbox is None:
            return 0
        if stream is None:
            safety = self.flush(now_ms, limit, stream=SAFETY_STREAM)
            if safety >= limit:
                return safety
            return safety + self.flush(
                now_ms, limit - safety, stream=OPERATIONAL_STREAM
            )
        if stream not in self._send_locks:
            raise ValueError("unknown delivery stream")
        delivered = 0
        timestamp_ms = now_ms if now_ms is not None else int(time.time() * 1_000)
        with self._send_locks[stream]:
            while delivered < limit:
                ready = self._outbox.ready(
                    timestamp_ms, limit - delivered, stream=stream
                )
                if not ready:
                    break
                progressed = False
                for item in ready:
                    with self._peers_lock:
                        peer = self._peers.get(item.peer_id)
                    if peer is None:
                        error = NetworkProtocolError(
                            f"outbox peer is not configured: {item.peer_id}"
                        )
                        self._outbox.failed(
                            item.peer_id,
                            item.envelope.message_id,
                            str(error),
                            timestamp_ms,
                        )
                        continue
                    try:
                        self._client.send(
                            peer.host,
                            peer.port,
                            peer.robot_id,
                            item.envelope,
                            peer.certificate_sha256,
                        )
                    except (OSError, ssl.SSLError, NetworkProtocolError) as error:
                        self._outbox.failed(
                            item.peer_id,
                            item.envelope.message_id,
                            f"{type(error).__name__}: {error}",
                            timestamp_ms,
                        )
                        with self._errors_lock:
                            self.delivery_errors.append((peer, error))
                            del self.delivery_errors[:-1_000]
                    else:
                        self._outbox.delivered(
                            item.peer_id, item.envelope.message_id, timestamp_ms
                        )
                        delivered += 1
                        progressed = True
                if not progressed:
                    break
        return delivered

    def _retry_loop(self) -> None:
        while not self._retry_stop.wait(0.2):
            self.flush(stream=SAFETY_STREAM)
            self.flush(stream=OPERATIONAL_STREAM)

    @staticmethod
    def _disclose(peer: PeerEndpoint, envelope: Envelope) -> Envelope | None:
        if envelope.message_type not in peer.allowed_message_types:
            return None
        if envelope.message_type != "manifest":
            return envelope
        allowed = set(peer.allowed_capabilities)
        filtered_payload = dict(envelope.payload)
        filtered_payload["capabilities"] = [
            capability
            for capability in envelope.payload.get("capabilities", ())
            if capability.get("name") in allowed
        ]
        return replace(envelope, payload=filtered_payload)

    def _receive_authenticated(self, envelope: Envelope) -> None:
        if envelope.source_id == self.robot_id:
            raise PeerIdentityError("remote connection claimed the local robot identity")
        self._local.publish(envelope)


@dataclass(frozen=True)
class DiscoveryAnnouncement:
    robot_id: str
    host: str
    port: int
    certificate_sha256: str
    expires_at_ms: int
    protocol: str = DISCOVERY_PROTOCOL

    def __post_init__(self) -> None:
        if not self.robot_id or len(self.robot_id.encode()) > 128:
            raise ValueError("invalid discovery robot_id")
        if not self.host or len(self.host.encode()) > 255:
            raise ValueError("invalid discovery host")
        if not 1 <= self.port <= 65_535:
            raise ValueError("invalid discovery port")
        fingerprint = self.certificate_sha256.lower()
        if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
            raise ValueError("certificate fingerprint must be 64 lowercase hex characters")
        if self.expires_at_ms < 0:
            raise ValueError("discovery expiry cannot be negative")


def encode_discovery(announcement: DiscoveryAnnouncement) -> bytes:
    encoded = json.dumps(
        announcement.__dict__,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_DISCOVERY_BYTES:
        raise NetworkProtocolError("discovery announcement is too large")
    return encoded


def decode_discovery(encoded: bytes, now_ms: int) -> DiscoveryAnnouncement:
    if not encoded or len(encoded) > MAX_DISCOVERY_BYTES:
        raise NetworkProtocolError("discovery announcement size is invalid")
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NetworkProtocolError("discovery announcement is not valid JSON") from error
    if not isinstance(value, dict):
        raise NetworkProtocolError("discovery announcement must be an object")
    required = {
        "robot_id", "host", "port", "certificate_sha256", "expires_at_ms", "protocol"
    }
    if missing := required - value.keys():
        raise NetworkProtocolError(f"discovery announcement is missing {sorted(missing)}")
    if value["protocol"] != DISCOVERY_PROTOCOL:
        raise NetworkProtocolError("unsupported discovery protocol")
    try:
        announcement = DiscoveryAnnouncement(**{key: value[key] for key in required})
    except (TypeError, ValueError) as error:
        raise NetworkProtocolError("invalid discovery announcement") from error
    if announcement.expires_at_ms < now_ms:
        raise NetworkProtocolError("discovery announcement has expired")
    return announcement


def send_discovery(
    announcement: DiscoveryAnnouncement,
    destination_host: str,
    destination_port: int,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.sendto(encode_discovery(announcement), (destination_host, destination_port))


def receive_discovery(
    bind_host: str,
    bind_port: int,
    *,
    timeout: float = 1.0,
    now_ms: int | None = None,
) -> tuple[DiscoveryAnnouncement, tuple[str, int]]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        connection.bind((bind_host, bind_port))
        connection.settimeout(timeout)
        encoded, sender = connection.recvfrom(MAX_DISCOVERY_BYTES + 1)
    current_ms = int(time.time() * 1_000) if now_ms is None else now_ms
    return decode_discovery(encoded, current_ms), sender
