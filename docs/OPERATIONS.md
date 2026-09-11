# Production Software Operations

This runbook covers deployment of UMP as software. It does not certify physical
robots, site safety, vendor autonomy, or emergency controls. A deployment is
release-ready only after the gates at the end of this document pass for the exact
revision and configuration being deployed.

## Deployment model

Run one `ump-node` process per robot identity and trusted manufacturer adapter.
Use a dedicated operating-system account, persistent local storage, owner-managed
PKI, and a private robot network. Keep every SQLite role on a distinct path.

The supported templates are:

- `docker/Dockerfile.production` for a minimal non-root runtime image;
- `deploy/compose.production.yml` for container deployments; and
- `deploy/systemd/*.service` for host-managed services.

Review all placeholder paths, adapter factories, resource limits, image names,
and secrets before use. Production deployments must reference a reviewed image
digest rather than a mutable tag.

## Preflight and startup

Build and validate the local image:

```sh
docker build -f docker/Dockerfile.production -t ump:local .
docker compose -f deploy/compose.production.yml config
```

Run `ump-node --preflight` with the same mounts, identity, adapter, network file,
credentials, and database paths that production will use. Preflight validates
configuration without binding sockets or creating runtime databases.

Start only after preflight succeeds. The node's JSON `ready` event means the
service listener and initial UMP publication started; supervisors should use the
operations endpoints for continuing status.

## Health, readiness, and metrics

Operations endpoints are disabled unless `--operations-port` is supplied. Bind
them to loopback or a private management interface:

```sh
ump-node ... --operations-host 127.0.0.1 --operations-port 9090
curl --fail http://127.0.0.1:9090/healthz
curl --fail http://127.0.0.1:9090/readyz
curl --fail http://127.0.0.1:9090/metrics
```

`/healthz` returns 503 after a recurring credential or inspector-recorder health
failure. `/readyz` returns 200 only after startup and returns 503 while stopping
or after a health failure. `/metrics` exposes content-free counters and gauges;
it contains no robot identity, task, pose, or telemetry fields.

Alert on a failed health probe, a failed readiness probe outside a controlled
restart, increasing `ump_runtime_failures_total`, stale peer diagnostics, inbox
dead letters, or an outbox retry backlog. Use `ump-network-diagnostics` for the
durable transport view.

## Inspector access

Loopback access is the default. Remote access fails closed unless all of these
are supplied: `--allow-remote`, `--auth-token-file`, `--tls-certificate`, and
`--tls-private-key`. The token must contain at least 32 bytes. Rotate it through
the deployment secret manager and terminate operator sessions after rotation.

The built-in authentication has one operator role. Put SSO, per-user policy,
rate limits, access logs, and public-edge protections in a reviewed reverse proxy
or identity-aware access layer. Never expose the inspector database or endpoint
directly to an untrusted network.

## Backup and restore

Create a consistent online backup and verify it immediately:

```sh
ump-ops backup \
  --database assignment=/var/lib/ump/assignments.sqlite3 \
  --database authority=/var/lib/ump/authority.sqlite3 \
  --database credentials=/var/lib/ump/credentials.sqlite3 \
  --database replay=/var/lib/ump/replay.sqlite3 \
  --database inbox=/var/lib/ump/inbox.sqlite3 \
  --database outbox=/var/lib/ump/outbox.sqlite3 \
  --database inspector=/var/lib/ump/inspector.sqlite3 \
  --output /backup/ump-2026-09-11
ump-ops verify-backup /backup/ump-2026-09-11/manifest.json
```

Encrypt backups, restrict access, copy them off-host, and retain them according
to the owner's policy. The manifest records each role, byte size, and SHA-256
checksum. Verification also runs SQLite integrity checks.

Restore only while UMP services are stopped. Restore writes to a new directory
and refuses to overwrite an existing target:

```sh
ump-ops restore /backup/ump-2026-09-11/manifest.json \
  --target-directory /restore/ump-2026-09-11
```

Verify the restored backup, update service paths atomically, run preflight, and
then start the service. Confirm credentials, authority leases, peer diagnostics,
inspector state, and uncertain-assignment reconciliation before returning it to
service. Test this entire procedure in staging on every release.

## Upgrade and rollback

1. Record the image digest, Git revision, configuration hashes, schema versions,
   and a verified backup.
2. Run unit, conformance, build, security, lab, load, and soak gates on that revision.
3. Drain new assignments, allow bounded work to finish, and stop the node cleanly.
4. Deploy the immutable image, run preflight, start, and verify readiness.
5. Confirm peer connectivity, retry/dead-letter counts, and inspector freshness.
6. On failure, stop the new process, restore the prior immutable image and known
   configuration, and restore data only when migration or corruption requires it.
7. Reconcile every assignment whose terminal outcome is uncertain. Never infer
   success and never dispatch uncertain work a second time.

## Software release gate

Do not label a revision production-ready until all applicable checks pass:

- clean source and wheel builds, package installation, and CLI smoke tests;
- complete unit tests and UMP 0.1 conformance vectors;
- Docker lab core, standards, fault, load, visual, and eight-hour soak profiles;
- readiness report generation and signature verification;
- dependency audit, secret scan, container scan, and SBOM generation;
- staging preflight, startup, probes, shutdown, restart, backup restore, and rollback;
- immutable artifact digests and release provenance retained with the release;
- reviewed capacity limits, retention policy, alert routing, incident owner, and
  credential-rotation procedure; and
- an independent threat review for the actual network and access topology.

Passing this gate qualifies the UMP software deployment for its declared
environment. Hardware readiness remains a separate, supervised process.
