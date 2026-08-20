from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sqlite3
import sys
from threading import Event
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .authority import SqliteAuthorityStore
from .benchmark import (
    run_reference_benchmark,
    run_tls_loopback_benchmark,
    run_tls_network_benchmark,
    serve_tls_network_benchmark,
)
from .conformance import AdapterConformanceHarness, validate_vector_suite
from .credentials import CredentialError, CredentialGeneration, SqliteCredentialStore
from .inspector import InspectorServer, InspectorStore
from .journal import SqliteAssignmentJournal
from .models import AssignmentStatus, AuthorityLease, payload
from .network import TlsNetworkBus, load_network_config
from .node import ParticipantService, load_adapter
from .readiness import load_readiness_report
from .vocabulary import standard_capability, vocabulary_document


def authority_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ump-authority",
        description="Manage robot-local UMP assignment authority leases.",
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--database", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    grant = commands.add_parser("grant", help="Create or renew a scoped lease")
    grant.add_argument("--lease-id", required=True)
    grant.add_argument("--issuer-id", required=True)
    grant.add_argument("--capability", action="append", required=True)
    grant.add_argument("--issued-at-ms", type=int)
    grant.add_argument("--expires-at-ms", type=int, required=True)
    grant.add_argument("--clock-uncertainty-ms", type=int, default=0)
    grant.add_argument("--revision", type=int, default=1)

    revoke = commands.add_parser("revoke", help="Revoke an active lease")
    revoke.add_argument("--lease-id", required=True)
    revoke.add_argument("--expected-revision", type=int, required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--occurred-at-ms", type=int)

    events = commands.add_parser("events", help="Print the lease audit history")
    events.add_argument("--lease-id", required=True)
    return parser


def authority_main(argv: list[str] | None = None) -> int:
    arguments = authority_parser().parse_args(argv)
    now_ms = int(time.time() * 1_000)
    store = SqliteAuthorityStore(arguments.robot_id, arguments.database)
    try:
        if arguments.command == "grant":
            issued_at_ms = arguments.issued_at_ms or now_ms
            lease = AuthorityLease(
                lease_id=arguments.lease_id,
                grantor_robot_id=arguments.robot_id,
                issuer_id=arguments.issuer_id,
                capabilities=tuple(arguments.capability),
                issued_at_ms=issued_at_ms,
                expires_at_ms=arguments.expires_at_ms,
                maximum_clock_uncertainty_ms=arguments.clock_uncertainty_ms,
                revision=arguments.revision,
            )
            store.grant(lease, now_ms)
            print(
                json.dumps(
                    {
                        "lease_id": lease.lease_id,
                        "revision": lease.revision,
                        "status": lease.status.value,
                    },
                    sort_keys=True,
                )
            )
        elif arguments.command == "revoke":
            occurred_at_ms = arguments.occurred_at_ms or now_ms
            store.revoke(
                arguments.lease_id,
                arguments.expected_revision,
                occurred_at_ms,
                arguments.reason,
            )
            print(
                json.dumps(
                    {
                        "lease_id": arguments.lease_id,
                        "revision": arguments.expected_revision + 1,
                        "status": "revoked",
                    },
                    sort_keys=True,
                )
            )
        else:
            events = [
                {
                    "sequence": event[0],
                    "event_type": event[1],
                    "revision": event[2],
                    "occurred_at_ms": event[3],
                    "detail": event[4],
                }
                for event in store.events(arguments.lease_id)
            ]
            print(json.dumps(events, sort_keys=True))
        return 0
    except (KeyError, ValueError, PermissionError) as error:
        print(f"ump-authority: {error}", file=sys.stderr)
        return 2
    finally:
        store.close()


def credentials_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ump-credentials",
        description="Manage robot-local UMP mutual-TLS credential generations.",
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--directory", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    enroll = commands.add_parser("enroll", help="Validate and stage an issued credential")
    enroll.add_argument("--certificate", required=True)
    enroll.add_argument("--private-key", required=True)
    enroll.add_argument("--ca", required=True)
    activate = commands.add_parser("activate", help="Activate a staged generation")
    activate.add_argument("--generation", type=int, required=True)
    revoke = commands.add_parser("revoke", help="Locally revoke a certificate fingerprint")
    revoke.add_argument("--fingerprint", required=True)
    revoke.add_argument("--reason", required=True)
    commands.add_parser("active", help="Print the active generation")
    commands.add_parser("events", help="Print the credential audit history")
    return parser


def _credential_document(generation: CredentialGeneration | None) -> object:
    if generation is None:
        return None
    return {
        "generation": generation.generation,
        "robot_id": generation.robot_id,
        "fingerprint_sha256": generation.fingerprint_sha256,
        "certificate_path": str(generation.certificate_path),
        "private_key_path": str(generation.private_key_path),
        "ca_path": str(generation.ca_path),
        "not_before_ms": generation.not_before_ms,
        "not_after_ms": generation.not_after_ms,
        "status": generation.status,
    }


def credentials_main(argv: list[str] | None = None) -> int:
    arguments = credentials_parser().parse_args(argv)
    now_ms = int(time.time() * 1_000)
    store: SqliteCredentialStore | None = None
    try:
        store = SqliteCredentialStore(
            arguments.robot_id, arguments.database, arguments.directory
        )
        if arguments.command == "enroll":
            result: object = _credential_document(
                store.enroll(
                    arguments.certificate,
                    arguments.private_key,
                    arguments.ca,
                    now_ms,
                )
            )
        elif arguments.command == "activate":
            result = _credential_document(store.activate(arguments.generation, now_ms))
        elif arguments.command == "revoke":
            store.revoke(arguments.fingerprint, arguments.reason, now_ms)
            result = {"fingerprint_sha256": arguments.fingerprint.lower(), "status": "revoked"}
        elif arguments.command == "active":
            result = _credential_document(store.active())
        else:
            result = store.events()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CredentialError, OSError, sqlite3.Error) as error:
        print(f"ump-credentials: {error}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def reconcile_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-reconcile",
        description="Resolve an unknown robot-local assignment from operator evidence.",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument(
        "--status",
        required=True,
        choices=("succeeded", "failed", "rejected", "cancelled"),
    )
    parser.add_argument("--description", required=True)
    parser.add_argument("--resolver-id", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--occurred-at-ms", type=int)
    arguments = parser.parse_args(argv)
    journal: SqliteAssignmentJournal | None = None
    try:
        journal = SqliteAssignmentJournal(arguments.database)
        outcome = journal.resolve_unknown(
            arguments.assignment_id,
            AssignmentStatus(arguments.status),
            arguments.description,
            arguments.resolver_id,
            arguments.evidence,
            arguments.occurred_at_ms or int(time.time() * 1_000),
        )
        print(json.dumps(payload(outcome), sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"ump-reconcile: {error}", file=sys.stderr)
        return 2
    finally:
        if journal is not None:
            journal.close()


def vocabulary_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-vocabulary",
        description="Inspect and validate the versioned UMP standard capability vocabulary.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List standard capability names and descriptions")
    show = commands.add_parser("show", help="Print one complete capability contract")
    show.add_argument("name")
    for command in ("validate-input", "validate-output"):
        validate = commands.add_parser(command, help=f"Validate a JSON {command[9:]}")
        validate.add_argument("name")
        validate.add_argument(
            "document",
            help="JSON document path, or - to read standard input",
        )
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "list":
            catalog = vocabulary_document()
            result: object = {
                "vocabulary": catalog["vocabulary"],
                "capabilities": [
                    {"name": item["name"], "description": item["description"]}
                    for item in catalog["capabilities"]
                ],
            }
        elif arguments.command == "show":
            capability = standard_capability(arguments.name)
            result = payload(capability)
        else:
            capability = standard_capability(arguments.name)
            encoded = (
                sys.stdin.read()
                if arguments.document == "-"
                else Path(arguments.document).read_text()
            )
            document = json.loads(encoded)
            schema = (
                capability.input_schema
                if arguments.command == "validate-input"
                else capability.output_schema
            )
            Draft202012Validator(schema).validate(document)
            result = {
                "capability": capability.name,
                "document": arguments.command[9:],
                "valid": True,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError, ValidationError) as error:
        print(f"ump-vocabulary: {error}", file=sys.stderr)
        return 2


def conformance_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-conformance",
        description="Validate a versioned UMP golden-vector suite.",
    )
    parser.add_argument("suite", help="Directory containing manifest.json")
    arguments = parser.parse_args(argv)
    try:
        report = validate_vector_suite(arguments.suite)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"ump-conformance: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "subject": report.subject,
                "passed": report.passed,
                "checks": [
                    {
                        "id": check.check_id,
                        "passed": check.passed,
                        "description": check.description,
                    }
                    for check in report.checks
                ],
            },
            sort_keys=True,
        )
    )
    return 0 if report.passed else 1


def benchmark_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-benchmark",
        description="Measure the UMP reference runtime quality targets.",
    )
    parser.add_argument("--samples", type=int, default=5_000)
    parser.add_argument("--participants", type=int, default=100)
    parser.add_argument(
        "--profile",
        choices=("in-memory", "tls-loopback"),
        default="in-memory",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.profile == "tls-loopback":
            report = run_tls_loopback_benchmark(samples=arguments.samples)
        else:
            report = run_reference_benchmark(
                samples=arguments.samples,
                participants=arguments.participants,
            )
    except ValueError as error:
        print(f"ump-benchmark: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0 if report.passed else 1


def lan_benchmark_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-lan-benchmark",
        description="Measure UMP mutual-TLS delivery between two network hosts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    server = commands.add_parser("server", help="Receive a bounded benchmark run")
    server.add_argument("--robot-id", required=True)
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, required=True)
    server.add_argument("--certificate", required=True)
    server.add_argument("--private-key", required=True)
    server.add_argument("--ca", required=True)
    server.add_argument("--samples", type=int, default=100)
    server.add_argument("--warmup-samples", type=int, default=10)
    server.add_argument("--timeout", type=float, default=120.0)

    client = commands.add_parser("client", help="Run and report a benchmark")
    client.add_argument("--robot-id", required=True)
    client.add_argument("--host", required=True)
    client.add_argument("--port", type=int, required=True)
    client.add_argument("--peer-id", required=True)
    client.add_argument("--peer-certificate-sha256")
    client.add_argument("--certificate", required=True)
    client.add_argument("--private-key", required=True)
    client.add_argument("--ca", required=True)
    client.add_argument("--samples", type=int, default=100)
    client.add_argument("--warmup-samples", type=int, default=10)
    client.add_argument("--timeout", type=float, default=5.0)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "server":
            if arguments.samples < 10:
                raise ValueError("samples must be at least 10")
            if not 0 <= arguments.warmup_samples <= 10_000:
                raise ValueError("warmup samples must be between 0 and 10000")

            def ready(host: str, port: int) -> None:
                print(
                    json.dumps(
                        {"event": "ready", "host": host, "port": port},
                        sort_keys=True,
                    ),
                    flush=True,
                )

            report = serve_tls_network_benchmark(
                host=arguments.host,
                port=arguments.port,
                robot_id=arguments.robot_id,
                certificate_path=arguments.certificate,
                private_key_path=arguments.private_key,
                ca_path=arguments.ca,
                expected_messages=arguments.samples + arguments.warmup_samples,
                timeout=arguments.timeout,
                ready=ready,
            )
        else:
            report = run_tls_network_benchmark(
                host=arguments.host,
                port=arguments.port,
                local_robot_id=arguments.robot_id,
                remote_robot_id=arguments.peer_id,
                certificate_path=arguments.certificate,
                private_key_path=arguments.private_key,
                ca_path=arguments.ca,
                samples=arguments.samples,
                warmup_samples=arguments.warmup_samples,
                timeout=arguments.timeout,
                expected_certificate_sha256=arguments.peer_certificate_sha256,
            ).as_dict()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ump-lan-benchmark: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


def node_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-node",
        description="Run one manufacturer adapter as a UMP network participant.",
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--adapter", required=True, help="Trusted module:factory")
    parser.add_argument("--adapter-config")
    parser.add_argument("--assignment-database", required=True)
    parser.add_argument("--authority-database", required=True)
    parser.add_argument("--state-hz", type=float, default=2.0)
    parser.add_argument("--execution-workers", type=int, default=0)
    arguments = parser.parse_args(argv)
    service = None
    stop = Event()
    previous_handlers = {}
    try:
        if not 1.0 <= arguments.state_hz <= 10.0:
            raise ValueError("state_hz must be between 1 and 10")
        if not 0 <= arguments.execution_workers <= 32:
            raise ValueError("execution_workers must be between 0 and 32")
        config = load_network_config(arguments.network)
        adapter = load_adapter(arguments.adapter, arguments.adapter_config)
        adapter_report = AdapterConformanceHarness().inspect(adapter)
        if not adapter_report.passed:
            failures = "; ".join(
                f"{check.check_id}: {check.description}"
                for check in adapter_report.checks
                if not check.passed
            )
            raise ValueError(f"adapter conformance failed: {failures}")
        if adapter.manifest().robot_id != config.robot_id:
            raise ValueError("adapter and network robot identities differ")
        journal = SqliteAssignmentJournal(arguments.assignment_database)
        authority = SqliteAuthorityStore(config.robot_id, arguments.authority_database)
        bus = TlsNetworkBus.from_config(config)
        try:
            service = ParticipantService(
                adapter,
                bus,
                journal,
                authority,
                state_hz=arguments.state_hz,
                execution_workers=arguments.execution_workers,
            )
        except BaseException:
            bus.stop()
            journal.close()
            authority.close()
            raise

        def request_stop(_signal_number, _frame) -> None:
            stop.set()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.signal(
                signal_number, request_stop
            )
        host, port = service.start()
        print(
            json.dumps(
                {
                    "event": "ready",
                    "robot_id": config.robot_id,
                    "host": host,
                    "port": port,
                    "state_hz": arguments.state_hz,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        service.run(stop)
        return 0
    except Exception as error:
        print(f"ump-node: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    finally:
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)
        if service is not None:
            service.close()


def readiness_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-readiness",
        description="Audit the UMP PRD requirement traceability matrix.",
    )
    parser.add_argument("project_root", nargs="?", default=".")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate matrix coverage and evidence paths without requiring readiness",
    )
    arguments = parser.parse_args(argv)
    try:
        report = load_readiness_report(arguments.project_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ump-readiness: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if arguments.validate_only or report["ready"] else 1


def inspector_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-inspector",
        description="Serve the local read-only UMP protocol event inspector.",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)
    store = InspectorStore(arguments.database)
    server = None
    try:
        server = InspectorServer(store, arguments.host, arguments.port)
        address = server.address
        print(f"UMP Inspector: http://{address.host}:{address.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        return 0
    except (OSError, ValueError) as error:
        print(f"ump-inspector: {error}", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.close()
        store.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "authority": authority_main,
        "benchmark": benchmark_main,
        "conformance": conformance_main,
        "credentials": credentials_main,
        "inspector": inspector_main,
        "lan-benchmark": lan_benchmark_main,
        "node": node_main,
        "reconcile": reconcile_main,
        "vocabulary": vocabulary_main,
        "readiness": readiness_main,
    }
    if not arguments or arguments[0] not in commands:
        print(
            "usage: python -m ump.cli {authority,benchmark,conformance,credentials,inspector,lan-benchmark,node,readiness,reconcile,vocabulary} ...",
            file=sys.stderr,
        )
        return 2
    return commands[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
