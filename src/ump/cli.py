from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
from threading import Event
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .authority import (
    EFFECTIVE_LEASE_STATUSES,
    AuthorityLeaseSummary,
    AuthorityReadError,
    SqliteAuthorityStore,
    read_authority_events,
    read_authority_lease,
    read_authority_leases,
)
from .adapter import CommunicationLossHandler
from .benchmark import (
    run_reference_benchmark,
    run_tls_loopback_benchmark,
    run_tls_network_benchmark,
    serve_tls_network_benchmark,
)
from .collaboration import Coordinator
from .conformance import (
    AdapterEvidenceValidationError,
    AdapterConformanceHarness,
    adapter_evidence_schema,
    inspect_adapter_evidence,
    validate_adapter_evidence,
    validate_vector_suite,
)
from .coordinator_node import (
    CoordinatorService,
    ParticipantContextTimeout,
    RunCompletionTimeout,
    parse_authority_leases,
)
from .coordinator_store import (
    CoordinatorStore,
    RunSnapshot,
    RunStatus,
    RunSummary,
    read_run_snapshot,
    read_run_summaries,
)
from .credentials import (
    CredentialError,
    CredentialGeneration,
    CredentialGenerationSummary,
    EFFECTIVE_CREDENTIAL_STATUSES,
    SqliteCredentialStore,
    read_credential_events,
    read_credential_generation,
    read_credential_generations,
    read_active_credential,
)
from .deployment import (
    DeploymentError,
    default_local_topology,
    deployment_topology_schema,
    generate_deployment_bundle,
    verify_local_awareness,
)
from .goal import (
    goal_batch_schema,
    goal_schema,
    goal_validation_report,
    shared_goal_from_document,
    shared_goals_from_document,
)
from .inspector import (
    InspectorRecorder,
    InspectorServer,
    InspectorStore,
    InspectorStoreError,
    ReadOnlyInspectorStore,
)
from .journal import SqliteAssignmentJournal
from .lan_evidence import (
    LanEvidenceValidationError,
    lan_evidence_schema,
    validate_lan_evidence_bundle,
)
from .models import AssignmentStatus, AuthorityLease, SharedGoal, payload
from .network import (
    TlsNetworkBus,
    create_client_context,
    create_server_context,
    load_network_config,
)
from .network_config import network_config_schema, validate_network_config
from .network_diagnostics import NetworkDiagnosticsError, inspect_network_databases
from .node import ParticipantService, load_adapter
from .planner import load_planner
from .runtime import Registry
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
    events.add_argument("--limit", type=int, default=1_000)
    leases = commands.add_parser("list", help="List bounded local lease inventory")
    leases.add_argument("--status", choices=sorted(EFFECTIVE_LEASE_STATUSES))
    leases.add_argument("--issuer-id")
    leases.add_argument("--limit", type=int, default=100)
    show = commands.add_parser("show", help="Print one exact local lease")
    show.add_argument("--lease-id", required=True)
    return parser


def _authority_lease_document(summary: AuthorityLeaseSummary) -> dict[str, object]:
    lease = summary.lease
    return {
        "lease_id": lease.lease_id,
        "grantor_robot_id": lease.grantor_robot_id,
        "issuer_id": lease.issuer_id,
        "capabilities": list(lease.capabilities),
        "issued_at_ms": lease.issued_at_ms,
        "expires_at_ms": lease.expires_at_ms,
        "maximum_clock_uncertainty_ms": lease.maximum_clock_uncertainty_ms,
        "revision": lease.revision,
        "stored_status": lease.status.value,
        "effective_status": summary.effective_status,
        "updated_at_ms": summary.updated_at_ms,
    }


def authority_main(argv: list[str] | None = None) -> int:
    arguments = authority_parser().parse_args(argv)
    now_ms = int(time.time() * 1_000)
    store = None
    try:
        if arguments.command == "grant":
            store = SqliteAuthorityStore(arguments.robot_id, arguments.database)
            issued_at_ms = (
                now_ms if arguments.issued_at_ms is None else arguments.issued_at_ms
            )
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
            store = SqliteAuthorityStore(arguments.robot_id, arguments.database)
            occurred_at_ms = (
                now_ms
                if arguments.occurred_at_ms is None
                else arguments.occurred_at_ms
            )
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
        elif arguments.command == "events":
            events = [
                {
                    "sequence": event[0],
                    "event_type": event[1],
                    "revision": event[2],
                    "occurred_at_ms": event[3],
                    "detail": event[4],
                }
                for event in read_authority_events(
                    arguments.database,
                    arguments.robot_id,
                    arguments.lease_id,
                    limit=arguments.limit,
                )
            ]
            print(json.dumps(events, sort_keys=True))
        elif arguments.command == "show":
            print(
                json.dumps(
                    _authority_lease_document(
                        read_authority_lease(
                            arguments.database,
                            arguments.robot_id,
                            arguments.lease_id,
                            now_ms,
                        )
                    ),
                    sort_keys=True,
                )
            )
        else:
            summaries = read_authority_leases(
                arguments.database,
                arguments.robot_id,
                now_ms,
                effective_status=arguments.status,
                issuer_id=arguments.issuer_id,
                limit=arguments.limit,
            )
            print(
                json.dumps(
                    [_authority_lease_document(summary) for summary in summaries],
                    sort_keys=True,
                )
            )
        return 0
    except (
        AuthorityReadError,
        KeyError,
        OSError,
        PermissionError,
        sqlite3.Error,
        ValueError,
    ) as error:
        print(f"ump-authority: {error}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def credentials_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ump-credentials",
        description="Manage robot-local UMP mutual-TLS credential generations.",
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--directory")
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
    commands.add_parser("active", help="Print the stored active generation")
    events = commands.add_parser("events", help="Print credential audit history")
    events.add_argument("--limit", type=int, default=1_000)
    generations = commands.add_parser("list", help="List bounded credential inventory")
    generations.add_argument("--status", choices=sorted(EFFECTIVE_CREDENTIAL_STATUSES))
    generations.add_argument("--limit", type=int, default=100)
    show = commands.add_parser("show", help="Print one exact credential generation")
    show.add_argument("--generation", type=int, required=True)
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


def _credential_summary_document(
    summary: CredentialGenerationSummary,
) -> dict[str, object]:
    document = _credential_document(summary.generation)
    assert isinstance(document, dict)
    return {
        **document,
        "stored_status": summary.generation.status,
        "effective_status": summary.effective_status,
        "revoked": summary.revoked,
        "created_at_ms": summary.created_at_ms,
        "activated_at_ms": summary.activated_at_ms,
    }


def credentials_main(argv: list[str] | None = None) -> int:
    arguments = credentials_parser().parse_args(argv)
    now_ms = int(time.time() * 1_000)
    store: SqliteCredentialStore | None = None
    try:
        if arguments.command in {"enroll", "activate", "revoke"}:
            if arguments.directory is None:
                raise CredentialError(
                    "--directory is required for credential mutation commands"
                )
            store = SqliteCredentialStore(
                arguments.robot_id, arguments.database, arguments.directory
            )
        if arguments.command == "enroll":
            assert store is not None
            result: object = _credential_document(
                store.enroll(
                    arguments.certificate,
                    arguments.private_key,
                    arguments.ca,
                    now_ms,
                )
            )
        elif arguments.command == "activate":
            assert store is not None
            result = _credential_document(store.activate(arguments.generation, now_ms))
        elif arguments.command == "revoke":
            assert store is not None
            store.revoke(arguments.fingerprint, arguments.reason, now_ms)
            result = {"fingerprint_sha256": arguments.fingerprint.lower(), "status": "revoked"}
        elif arguments.command == "active":
            summaries = read_credential_generations(
                arguments.database,
                arguments.robot_id,
                now_ms,
                stored_status="active",
                limit=1,
            )
            result = _credential_summary_document(summaries[0]) if summaries else None
        elif arguments.command == "show":
            result = _credential_summary_document(
                read_credential_generation(
                    arguments.database,
                    arguments.robot_id,
                    arguments.generation,
                    now_ms,
                )
            )
        elif arguments.command == "list":
            result = [
                _credential_summary_document(summary)
                for summary in read_credential_generations(
                    arguments.database,
                    arguments.robot_id,
                    now_ms,
                    effective_status=arguments.status,
                    limit=arguments.limit,
                )
            ]
        else:
            result = read_credential_events(
                arguments.database,
                arguments.robot_id,
                limit=arguments.limit,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CredentialError, OSError, sqlite3.Error) as error:
        print(f"ump-credentials: {error}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


def _prompt_value(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def deployment_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-deployment",
        description="Generate and verify UMP robot deployment bundles.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate from a topology JSON file")
    generate.add_argument("--topology", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--development-pki", action="store_true")
    quickstart = commands.add_parser("quickstart", help="Generate a local awareness lab")
    quickstart.add_argument("--output", required=True)
    quickstart.add_argument("--robots", type=int, default=3)
    quickstart.add_argument("--base-port", type=int, default=17443)
    wizard = commands.add_parser("wizard", help="Interactively define a local awareness lab")
    wizard.add_argument("--output", required=True)
    wizard.add_argument("--base-port", type=int, default=17443)
    verify = commands.add_parser("verify-local", help="Run mutual-TLS peer awareness checks")
    verify.add_argument("bundle_root")
    commands.add_parser("schema", help="Print the deployment topology schema")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "schema":
            result = deployment_topology_schema()
        elif arguments.command == "verify-local":
            result = verify_local_awareness(arguments.bundle_root)
        elif arguments.command == "generate":
            result = generate_deployment_bundle(
                arguments.topology,
                arguments.output,
                development_pki=arguments.development_pki,
            )
        elif arguments.command == "quickstart":
            result = generate_deployment_bundle(
                default_local_topology(arguments.robots, arguments.base_port),
                arguments.output,
                development_pki=True,
            )
        else:
            count = int(_prompt_value("Number of robots", "3"))
            topology = default_local_topology(count, arguments.base_port)
            for index, node in enumerate(topology["nodes"], 1):
                print(f"Robot {index}", file=sys.stderr)
                node["robot_id"] = _prompt_value("  Robot ID", node["robot_id"])
                node["manufacturer"] = _prompt_value(
                    "  Manufacturer", node["manufacturer"]
                )
                node["model"] = _prompt_value("  Model", node["model"])
                node["robot_class"] = _prompt_value(
                    "  Robot class", node["robot_class"]
                )
            result = generate_deployment_bundle(
                topology, arguments.output, development_pki=True
            )
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("passed", True) else 1
    except (DeploymentError, OSError, ValueError, EOFError) as error:
        print(f"ump-deployment: {error}", file=sys.stderr)
        return 2


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


def adapter_conformance_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-adapter-conformance",
        description="Generate read-only manufacturer adapter conformance evidence.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_command = commands.add_parser(
        "inspect", help="Read an adapter manifest and semantic state"
    )
    inspect_command.add_argument("--adapter", required=True)
    inspect_command.add_argument("--adapter-config")
    inspect_command.add_argument("--revision", required=True)
    inspect_command.add_argument("--output")
    verify = commands.add_parser("verify", help="Verify one retained report")
    verify.add_argument("report")
    verify.add_argument("--implementation")
    commands.add_parser("schema", help="Print the adapter evidence JSON Schema")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "schema":
            print(json.dumps(adapter_evidence_schema(), sort_keys=True))
            return 0
        if arguments.command == "verify":
            result = validate_adapter_evidence(
                arguments.report,
                implementation_path=arguments.implementation,
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["passed"] else 1
        adapter = load_adapter(arguments.adapter, arguments.adapter_config)
        result = inspect_adapter_evidence(
            adapter,
            arguments.adapter,
            repository_revision=arguments.revision,
        )
        encoded = json.dumps(result, sort_keys=True)
        if arguments.output is not None:
            output = Path(arguments.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=output.parent,
                    prefix=f".{output.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary.write(encoded + "\n")
                    temporary_path = Path(temporary.name)
                temporary_path.replace(output)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        print(encoded)
        return 0 if result["passed"] else 1
    except (AdapterEvidenceValidationError, OSError, TypeError, ValueError) as error:
        print(f"ump-adapter-conformance: {error}", file=sys.stderr)
        return 2


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
    server.add_argument("--revision", required=True)
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
    client.add_argument("--revision", required=True)
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
                repository_revision=arguments.revision,
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
                repository_revision=arguments.revision,
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


def _validate_database_roles(database_paths: dict[str, Path]) -> None:
    paths_to_roles: dict[Path, list[str]] = {}
    for role, path in database_paths.items():
        paths_to_roles.setdefault(path.resolve(), []).append(role)
    collisions = {
        str(path): roles for path, roles in paths_to_roles.items() if len(roles) > 1
    }
    if collisions:
        raise ValueError(f"database paths must be unique by role: {collisions}")


def _validate_storage_parents(database_paths: dict[str, Path]) -> None:
    for role, path in database_paths.items():
        parent = path.resolve().parent
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            raise ValueError(
                f"{role} database parent is not a writable directory: {parent}"
            )


def _validate_private_key_permissions(path: Path) -> None:
    if path.stat().st_mode & 0o077:
        raise ValueError("network private key must not be group/world accessible")


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
    parser.add_argument("--credential-database", required=True)
    parser.add_argument("--credential-directory", required=True)
    parser.add_argument("--inspector-database")
    parser.add_argument("--state-hz", type=float, default=2.0)
    parser.add_argument("--execution-workers", type=int, default=0)
    parser.add_argument("--required-peer", action="append", default=[])
    parser.add_argument("--communication-check-interval", type=float, default=0.25)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate deployment inputs without binding sockets or creating stores",
    )
    arguments = parser.parse_args(argv)
    service = None
    credentials = None
    journal = None
    authority = None
    bus = None
    inspector_store = None
    inspector_recorder = None
    stop = Event()
    previous_handlers = {}
    try:
        if not 1.0 <= arguments.state_hz <= 10.0:
            raise ValueError("state_hz must be between 1 and 10")
        if not 0 <= arguments.execution_workers <= 32:
            raise ValueError("execution_workers must be between 0 and 32")
        if len(arguments.required_peer) != len(set(arguments.required_peer)):
            raise ValueError("required peers must be unique")
        if not 0.05 <= arguments.communication_check_interval <= 60.0:
            raise ValueError(
                "communication check interval must be between 0.05 and 60 seconds"
            )
        config = load_network_config(arguments.network)
        database_paths = {
            "assignment": Path(arguments.assignment_database).resolve(),
            "authority": Path(arguments.authority_database).resolve(),
            "credential": Path(arguments.credential_database).resolve(),
            "replay": config.replay_database_path.resolve(),
            "inbox": config.inbox_database_path.resolve(),
            "outbox": config.outbox_database_path.resolve(),
        }
        if arguments.inspector_database is not None:
            database_paths["inspector"] = Path(arguments.inspector_database).resolve()
        _validate_database_roles(database_paths)
        configured_peer_ids = {peer.robot_id for peer in config.peers}
        unknown_required_peers = set(arguments.required_peer) - configured_peer_ids
        if unknown_required_peers:
            raise ValueError(
                f"required peers are not configured: {sorted(unknown_required_peers)}"
            )
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
        if arguments.required_peer and not isinstance(
            adapter, CommunicationLossHandler
        ):
            raise TypeError(
                "adapter must implement CommunicationLossHandler when required peers are configured"
            )
        _validate_storage_parents(database_paths)
        _validate_private_key_permissions(config.private_key_path)
        if arguments.preflight:
            generation = read_active_credential(
                arguments.credential_database,
                config.robot_id,
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            create_server_context(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            create_client_context(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            print(
                json.dumps(
                    {
                        "valid": True,
                        "mode": "preflight",
                        "robot_id": config.robot_id,
                        "adapter_subject": adapter_report.subject,
                        "credential_generation": generation.generation,
                        "configured_peers": len(config.peers),
                        "database_roles": sorted(database_paths),
                        "checks": [
                            "network_configuration",
                            "adapter_read_only_conformance",
                            "adapter_network_identity",
                            "required_peer_policy",
                            "active_credential_validity",
                            "tls_contexts",
                            "private_key_permissions",
                            "database_role_isolation",
                            "storage_parent_permissions",
                            *(
                                ["protocol_event_recording"]
                                if arguments.inspector_database is not None
                                else []
                            ),
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 0
        credentials = SqliteCredentialStore(
            config.robot_id,
            arguments.credential_database,
            arguments.credential_directory,
        )
        credentials.require_active_bundle(
            config.certificate_path,
            config.private_key_path,
            config.ca_path,
        )
        journal = SqliteAssignmentJournal(arguments.assignment_database)
        authority = SqliteAuthorityStore(config.robot_id, arguments.authority_database)
        bus = TlsNetworkBus.from_config(
            config, certificate_revoked=credentials.is_revoked
        )
        if arguments.inspector_database is not None:
            inspector_store = InspectorStore(arguments.inspector_database)
            inspector_recorder = InspectorRecorder(bus, inspector_store)

        def require_runtime_health() -> None:
            credentials.require_active_bundle(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            if inspector_recorder is not None:
                inspector_recorder.require_healthy()

        service = ParticipantService(
            adapter,
            bus,
            journal,
            authority,
            state_hz=arguments.state_hz,
            execution_workers=arguments.execution_workers,
            health_check=require_runtime_health,
            required_peer_ids=tuple(arguments.required_peer),
            communication_check_interval_s=arguments.communication_check_interval,
        )

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
                    "inspector_recording": inspector_store is not None,
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
        else:
            if bus is not None:
                bus.stop()
            if journal is not None:
                journal.close()
            if authority is not None:
                authority.close()
        if credentials is not None:
            credentials.close()
        if inspector_store is not None:
            inspector_store.close()


def network_config_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-network-config",
        description="Validate a strict UMP mutual-TLS network configuration.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate one network JSON file")
    validate.add_argument("config")
    commands.add_parser("schema", help="Print the network configuration JSON Schema")
    arguments = parser.parse_args(argv)
    try:
        result = (
            network_config_schema()
            if arguments.command == "schema"
            else validate_network_config(arguments.config)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"ump-network-config: {error}", file=sys.stderr)
        return 2


def network_diagnostics_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-network-diagnostics",
        description="Inspect durable UMP delivery health without mutating stores.",
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--maximum-pending-age-ms", type=int, default=60_000)
    parser.add_argument("--detail-limit", type=int, default=20)
    arguments = parser.parse_args(argv)
    try:
        config = load_network_config(arguments.network)
        result = inspect_network_databases(
            config.inbox_database_path,
            config.outbox_database_path,
            maximum_pending=config.maximum_pending_deliveries,
            reserved_safety=config.reserved_safety_deliveries,
            observed_at_ms=int(time.time() * 1_000),
            maximum_pending_age_ms=arguments.maximum_pending_age_ms,
            detail_limit=arguments.detail_limit,
        )
        result["robot_id"] = config.robot_id
        print(json.dumps(result, sort_keys=True))
        return 0 if result["healthy"] else 1
    except (NetworkDiagnosticsError, OSError, ValueError) as error:
        print(f"ump-network-diagnostics: {error}", file=sys.stderr)
        return 2


def goal_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-goal",
        description="Validate strict UMP shared-goal documents.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate one shared goal")
    validate.add_argument("goal")
    validate_batch = commands.add_parser(
        "validate-batch", help="Validate an ordered batch of shared goals"
    )
    validate_batch.add_argument("goals")
    commands.add_parser("schema", help="Print the shared-goal JSON Schema")
    commands.add_parser("batch-schema", help="Print the goal-batch JSON Schema")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "schema":
            result = goal_schema()
        elif arguments.command == "batch-schema":
            result = goal_batch_schema()
        elif arguments.command == "validate-batch":
            result = goal_validation_report(
                _load_json_document(arguments.goals), batch=True
            )
        else:
            result = goal_validation_report(_load_json_document(arguments.goal))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ump-goal: {error}", file=sys.stderr)
        return 2


def _run_snapshot_document(snapshot: RunSnapshot) -> dict[str, object]:
    return {
        "plan_id": snapshot.plan_id,
        "goal_id": snapshot.goal_id,
        "status": snapshot.status.value,
        "steps": {step_id: status.value for step_id, status in snapshot.steps.items()},
    }


def _run_summary_document(summary: RunSummary) -> dict[str, object]:
    return {
        "plan_id": summary.plan_id,
        "goal_id": summary.goal_id,
        "status": summary.status.value,
        "created_at_ms": summary.created_at_ms,
        "updated_at_ms": summary.updated_at_ms,
    }


def _load_json_document(path: str):
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _shared_goal(document) -> SharedGoal:
    return shared_goal_from_document(document)


def _load_shared_goal(path: str) -> SharedGoal:
    return _shared_goal(_load_json_document(path))


def _load_shared_goals(path: str) -> tuple[SharedGoal, ...]:
    return shared_goals_from_document(_load_json_document(path))


def coordinator_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ump-coordinator",
        description="Submit and inspect durable UMP collaborative runs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    submit = commands.add_parser(
        "submit", help="Submit one shared goal over the secure UMP network"
    )

    def add_network_runtime(command) -> None:
        command.add_argument("--network", required=True)
        command.add_argument("--database", required=True)
        command.add_argument("--credential-database", required=True)
        command.add_argument("--credential-directory", required=True)
        command.add_argument("--inspector-database")

    add_network_runtime(submit)
    submit.add_argument("--planner", required=True, help="Trusted module:factory")
    submit.add_argument("--planner-config")
    goal_input = submit.add_mutually_exclusive_group(required=True)
    goal_input.add_argument("--goal", help="One goal JSON path, or - for stdin")
    goal_input.add_argument("--goals", help="Goal array JSON path, or - for stdin")
    submit.add_argument(
        "--authority-lease",
        action="append",
        default=[],
        metavar="ROBOT_ID=LEASE_ID",
    )
    submit.add_argument("--participant-timeout", type=float, default=30.0)
    submit.add_argument("--completion-timeout", type=float, default=300.0)
    cancel = commands.add_parser(
        "cancel", help="Request native cancellation for one durable run"
    )
    add_network_runtime(cancel)
    cancel.add_argument("--plan-id", required=True)
    cancel.add_argument("--reason", required=True)
    cancel.add_argument("--completion-timeout", type=float, default=30.0)
    reconcile = commands.add_parser(
        "reconcile", help="Query robots for durable evidence of uncertain work"
    )
    add_network_runtime(reconcile)
    reconcile.add_argument("--plan-id", required=True)
    reconcile.add_argument("--participant-timeout", type=float, default=30.0)
    reconcile.add_argument("--completion-timeout", type=float, default=300.0)
    preflight = commands.add_parser(
        "preflight", help="Validate deployment inputs without starting a service"
    )
    add_network_runtime(preflight)
    status = commands.add_parser("status", help="Read one run from the durable journal")
    status.add_argument("--database", required=True)
    status.add_argument("--plan-id", required=True)
    runs = commands.add_parser("runs", help="List bounded durable run history")
    runs.add_argument("--database", required=True)
    runs.add_argument("--status", choices=tuple(item.value for item in RunStatus))
    runs.add_argument("--limit", type=int, default=100)
    return parser


def coordinator_main(argv: list[str] | None = None) -> int:
    arguments = coordinator_parser().parse_args(argv)
    if arguments.command == "status":
        try:
            snapshot = read_run_snapshot(arguments.database, arguments.plan_id)
            print(json.dumps(_run_snapshot_document(snapshot), sort_keys=True))
            return 0
        except (KeyError, OSError, sqlite3.Error, ValueError) as error:
            print(f"ump-coordinator: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
    if arguments.command == "runs":
        try:
            selected_status = (
                RunStatus(arguments.status) if arguments.status is not None else None
            )
            summaries = read_run_summaries(
                arguments.database,
                status=selected_status,
                limit=arguments.limit,
            )
            print(
                json.dumps(
                    [_run_summary_document(summary) for summary in summaries],
                    sort_keys=True,
                )
            )
            return 0
        except (OSError, sqlite3.Error, ValueError) as error:
            print(f"ump-coordinator: {type(error).__name__}: {error}", file=sys.stderr)
            return 2

    service = None
    credentials = None
    store = None
    bus = None
    inspector_store = None
    inspector_recorder = None
    stop = Event()
    previous_handlers = {}
    plan_id: str | None = None
    plan_ids: tuple[str, ...] = ()
    try:
        config = load_network_config(arguments.network)
        database_paths = {
            "coordinator": Path(arguments.database).resolve(),
            "credential": Path(arguments.credential_database).resolve(),
            "replay": config.replay_database_path.resolve(),
            "inbox": config.inbox_database_path.resolve(),
            "outbox": config.outbox_database_path.resolve(),
        }
        if arguments.inspector_database is not None:
            database_paths["inspector"] = Path(arguments.inspector_database).resolve()
        _validate_database_roles(database_paths)
        _validate_storage_parents(database_paths)
        _validate_private_key_permissions(config.private_key_path)
        if arguments.command == "preflight":
            generation = read_active_credential(
                arguments.credential_database,
                config.robot_id,
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            create_server_context(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            create_client_context(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            print(
                json.dumps(
                    {
                        "valid": True,
                        "mode": "preflight",
                        "coordinator_id": config.robot_id,
                        "credential_generation": generation.generation,
                        "configured_peers": len(config.peers),
                        "database_roles": sorted(database_paths),
                        "checks": [
                            "network_configuration",
                            "active_credential_validity",
                            "tls_contexts",
                            "private_key_permissions",
                            "database_role_isolation",
                            "storage_parent_permissions",
                            *(
                                ["protocol_event_recording"]
                                if arguments.inspector_database is not None
                                else []
                            ),
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 0
        goal = None
        goals = None
        planner = None
        authority_leases = {}
        if arguments.command == "submit":
            if arguments.goal is not None:
                goal = _load_shared_goal(arguments.goal)
                goals = (goal,)
            else:
                goals = _load_shared_goals(arguments.goals)
            planner = load_planner(arguments.planner, arguments.planner_config)
            authority_leases = parse_authority_leases(arguments.authority_lease)
            participant_ids = {
                participant_id
                for item in goals
                for participant_id in item.participant_ids
            }
            unknown_participants = participant_ids - {
                peer.robot_id for peer in config.peers
            }
            if unknown_participants:
                raise ValueError(
                    "goal participants are not configured peers: "
                    f"{sorted(unknown_participants)}"
                )
        credentials = SqliteCredentialStore(
            config.robot_id,
            arguments.credential_database,
            arguments.credential_directory,
        )
        credentials.require_active_bundle(
            config.certificate_path,
            config.private_key_path,
            config.ca_path,
        )
        bus = TlsNetworkBus.from_config(
            config, certificate_revoked=credentials.is_revoked
        )
        if arguments.inspector_database is not None:
            inspector_store = InspectorStore(arguments.inspector_database)
            inspector_recorder = InspectorRecorder(bus, inspector_store)
        registry = Registry(bus)
        store = CoordinatorStore(
            arguments.database,
            recover_interrupted=arguments.command != "cancel",
        )
        if arguments.command in {"cancel", "reconcile"}:
            plan = store.plan(arguments.plan_id)
            assigned_robot_ids = {step.assigned_robot_id for step in plan.steps}
            unknown_targets = assigned_robot_ids - {
                peer.robot_id for peer in config.peers
            }
            if unknown_targets:
                raise ValueError(
                    f"assigned robots are not configured peers: {sorted(unknown_targets)}"
                )
        coordinator = Coordinator(
            config.robot_id,
            bus,
            registry,
            store=store,
            authority_lease_ids=authority_leases,
        )

        def require_runtime_health() -> None:
            credentials.require_active_bundle(
                config.certificate_path,
                config.private_key_path,
                config.ca_path,
            )
            if inspector_recorder is not None:
                inspector_recorder.require_healthy()

        service = CoordinatorService(
            bus,
            coordinator,
            registry,
            health_check=require_runtime_health,
        )

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
                    "coordinator_id": config.robot_id,
                    "host": host,
                    "port": port,
                    "inspector_recording": inspector_store is not None,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if arguments.command == "cancel":
            plan_id = arguments.plan_id
            assignment_ids = service.cancel(
                plan_id,
                arguments.reason,
                stop=stop,
            )
            snapshot = coordinator.snapshot(plan_id)
            print(
                json.dumps(
                    {
                        "event": "cancellation_requested",
                        "assignment_ids": assignment_ids,
                        **_run_snapshot_document(snapshot),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if snapshot.status is RunStatus.ACTIVE:
                snapshot = service.wait_for_completion(
                    plan_id, arguments.completion_timeout, stop
                )
            print(
                json.dumps(_run_snapshot_document(snapshot), sort_keys=True),
                flush=True,
            )
            return 0 if snapshot.status is RunStatus.CANCELLED else 1
        if arguments.command == "reconcile":
            plan_id = arguments.plan_id
            assignment_ids = service.reconcile(
                plan_id,
                participant_timeout_s=arguments.participant_timeout,
                stop=stop,
            )
            snapshot = coordinator.snapshot(plan_id)
            print(
                json.dumps(
                    {
                        "event": "reconciliation_requested",
                        "assignment_ids": assignment_ids,
                        **_run_snapshot_document(snapshot),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            snapshot = service.wait_for_resolution(
                plan_id, arguments.completion_timeout, stop
            )
            print(
                json.dumps(_run_snapshot_document(snapshot), sort_keys=True),
                flush=True,
            )
            return 0
        assert goals is not None and planner is not None
        if arguments.goals is not None:
            plans = service.submit_many(
                goals,
                planner,
                participant_timeout_s=arguments.participant_timeout,
                stop=stop,
            )
            plan_ids = tuple(plan.plan_id for plan in plans)
            snapshots = tuple(coordinator.snapshot(item) for item in plan_ids)
            print(
                json.dumps(
                    {
                        "event": "submitted_batch",
                        "runs": [_run_snapshot_document(item) for item in snapshots],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            snapshots = service.wait_for_completions(
                plan_ids, arguments.completion_timeout, stop
            )
            print(
                json.dumps(
                    {
                        "event": "completed_batch",
                        "runs": [_run_snapshot_document(item) for item in snapshots],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return (
                0
                if all(item.status is RunStatus.SUCCEEDED for item in snapshots)
                else 1
            )
        assert goal is not None
        plan = service.submit(
            goal,
            planner,
            participant_timeout_s=arguments.participant_timeout,
            stop=stop,
        )
        plan_id = plan.plan_id
        plan_ids = (plan.plan_id,)
        snapshot = coordinator.snapshot(plan.plan_id)
        print(
            json.dumps(
                {"event": "submitted", **_run_snapshot_document(snapshot)},
                sort_keys=True,
            ),
            flush=True,
        )
        snapshot = service.wait_for_completion(
            plan.plan_id, arguments.completion_timeout, stop
        )
        print(json.dumps(_run_snapshot_document(snapshot), sort_keys=True), flush=True)
        return 0 if snapshot.status is RunStatus.SUCCEEDED else 1
    except (ParticipantContextTimeout, RunCompletionTimeout, InterruptedError) as error:
        detail: dict[str, object] = {"error": str(error)}
        if plan_id is not None and service is not None:
            detail["run"] = _run_snapshot_document(service.coordinator.snapshot(plan_id))
        elif plan_ids and service is not None:
            detail["runs"] = [
                _run_snapshot_document(service.coordinator.snapshot(item))
                for item in plan_ids
            ]
        print(json.dumps(detail, sort_keys=True), file=sys.stderr, flush=True)
        return 3
    except Exception as error:
        print(f"ump-coordinator: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    finally:
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)
        if service is not None:
            service.close()
        else:
            if bus is not None:
                bus.stop()
            if store is not None:
                store.close()
        if credentials is not None:
            credentials.close()
        if inspector_store is not None:
            inspector_store.close()


def lan_evidence_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-lan-evidence",
        description="Validate integrity-bound two-host LAN benchmark evidence.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate one evidence manifest")
    validate.add_argument("manifest")
    commands.add_parser("schema", help="Print the LAN evidence JSON Schema")
    arguments = parser.parse_args(argv)
    try:
        result = (
            lan_evidence_schema()
            if arguments.command == "schema"
            else validate_lan_evidence_bundle(arguments.manifest)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except LanEvidenceValidationError as error:
        print(f"ump-lan-evidence: {error}", file=sys.stderr)
        return 2


def inspector_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-inspector",
        description="Serve the local read-only UMP protocol event inspector.",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)
    store = None
    server = None
    try:
        store = ReadOnlyInspectorStore(arguments.database)
        server = InspectorServer(store, arguments.host, arguments.port)
        address = server.address
        print(f"UMP Inspector: http://{address.host}:{address.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        return 0
    except (InspectorStoreError, OSError, sqlite3.Error, ValueError) as error:
        print(f"ump-inspector: {error}", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.close()
        if store is not None:
            store.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "authority": authority_main,
        "adapter-conformance": adapter_conformance_main,
        "benchmark": benchmark_main,
        "conformance": conformance_main,
        "coordinator": coordinator_main,
        "credentials": credentials_main,
        "deployment": deployment_main,
        "goal": goal_main,
        "inspector": inspector_main,
        "lan-benchmark": lan_benchmark_main,
        "lan-evidence": lan_evidence_main,
        "node": node_main,
        "network-config": network_config_main,
        "network-diagnostics": network_diagnostics_main,
        "reconcile": reconcile_main,
        "vocabulary": vocabulary_main,
    }
    if not arguments or arguments[0] not in commands:
        choices = ",".join(commands)
        print(
            f"usage: python -m ump.cli {{{choices}}} ...",
            file=sys.stderr,
        )
        return 2
    return commands[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
