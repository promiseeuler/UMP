# Production deployment templates

These templates package the existing UMP 0.1 service without changing protocol
or adapter contracts. Review and pin every image, path, identity, and resource
limit for the target environment.

## Container workflow

Set `UMP_ADAPTER`, `UMP_CONFIG_DIR`, `UMP_ADAPTER_DIR`, and
`UMP_INSPECTOR_SECRET_DIR`, then validate before starting:

```sh
docker compose -f deploy/compose.production.yml config
docker compose -f deploy/compose.production.yml run --rm participant \
  ump-node --preflight \
  --network /etc/ump/network.json \
  --adapter "$UMP_ADAPTER" \
  --adapter-config /etc/ump/adapter.json \
  --assignment-database /var/lib/ump/assignments.sqlite3 \
  --authority-database /var/lib/ump/authority.sqlite3 \
  --credential-database /var/lib/ump/credentials.sqlite3 \
  --credential-directory /var/lib/ump/credentials \
  --inspector-database /var/lib/ump/inspector.sqlite3
docker compose -f deploy/compose.production.yml up -d
```

The inspector token must contain at least 32 random bytes. The inspector TLS
certificate and key are deployment-owned. The default published addresses are
loopback; use a private management network or authenticated reverse proxy for
remote operators.

## Data operations

Stop the participant or coordinate a maintenance window before restore. Online
backup uses SQLite's consistent backup API:

```sh
ump-ops backup \
  --database assignment=/var/lib/ump/assignments.sqlite3 \
  --database authority=/var/lib/ump/authority.sqlite3 \
  --database credentials=/var/lib/ump/credentials.sqlite3 \
  --database inspector=/var/lib/ump/inspector.sqlite3 \
  --output /backup/ump-2026-09-11
ump-ops verify-backup /backup/ump-2026-09-11/manifest.json
ump-ops restore /backup/ump-2026-09-11/manifest.json \
  --target-directory /restore/ump-2026-09-11
```

## systemd workflow

Create an `ump` service account, install UMP into `/opt/ump`, install the trusted
adapter under `/opt/ump-adapters`, and place reviewed configuration under
`/etc/ump`. Replace the example adapter factory in both unit files before
installation. Keep the inspector on loopback behind the operator access layer.

Validate backup restoration and rollback in staging before every production
upgrade. A UMP process restart must never cause native work with an unknown
outcome to be dispatched again.
