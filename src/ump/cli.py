from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

from .authority import SqliteAuthorityStore
from .benchmark import run_reference_benchmark
from .conformance import validate_vector_suite
from .credentials import CredentialError, CredentialGeneration, SqliteCredentialStore
from .inspector import InspectorServer, InspectorStore
from .models import AuthorityLease
from .readiness import load_readiness_report


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
    arguments = parser.parse_args(argv)
    try:
        report = run_reference_benchmark(
            samples=arguments.samples,
            participants=arguments.participants,
        )
    except ValueError as error:
        print(f"ump-benchmark: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0 if report.passed else 1


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
        "readiness": readiness_main,
    }
    if not arguments or arguments[0] not in commands:
        print(
            "usage: python -m ump.cli {authority,benchmark,conformance,credentials,inspector,readiness} ...",
            file=sys.stderr,
        )
        return 2
    return commands[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
