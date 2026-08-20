# UMP Credential Lifecycle Profile v0.1

UMP does not issue trust. Robot owners and manufacturers obtain leaf certificates
from their established PKI, then use the robot-local credential store to validate,
inventory, rotate, and revoke them. A leaf certificate must contain exactly one
`urn:ump:robot:<robot-id>` URI subject alternative name. Its unencrypted private
key must match the certificate.

## Enrollment and rotation

Enrollment validates the PEM objects, robot identity, key pair, and validity
window before copying them into a private, generation-specific directory. It
does not activate the generation:

```sh
ump-credentials --robot-id robot-1 \
  --database var/credentials.db --directory var/credentials \
  enroll --certificate issued/robot-1.pem \
  --private-key issued/robot-1.key --ca issued/site-ca.pem
```

After operational approval, activate the generation printed by enrollment:

```sh
ump-credentials --robot-id robot-1 \
  --database var/credentials.db --directory var/credentials \
  activate --generation 2
```

Activation retires the prior generation and activates the selected generation in
one durable transaction. `active` prints the exact paths to place in the network
configuration. Restart the UMP network bus after local activation because Python
TLS contexts load the local certificate and key at construction time. Keep the
prior generation available during a planned overlap window so peers can receive
the new fingerprint before activation.

Private keys are copied with mode `0600`; the managed directory and generation
directories use `0700`. Deployments must additionally use encrypted disks,
restricted service accounts, and a hardware-backed key provider where their risk
model requires one. The reference profile supports unencrypted PEM keys because
Python's unattended TLS server requires a non-interactive key-loading strategy.

## Revocation

```sh
ump-credentials --robot-id robot-1 \
  --database var/credentials.db --directory var/credentials \
  revoke --fingerprint <sha256> --reason "device decommissioned"
```

Revocation is local and fingerprint based. Pass `store.is_revoked` as the
`certificate_revoked` callback when constructing `TlsNetworkBus`,
`TlsMessageServer`, or `TlsMessageClient`. Both inbound and outbound handshakes
check it after CA-chain validation and before any UMP frame is accepted or sent.
The callback is evaluated on every connection, so peer revocations apply without
recreating TLS contexts.

Revoking the active local certificate retires it immediately in the inventory.
Operators must stop the bus and rotate to a valid generation; continuing to use a
locally revoked identity is an operational fault. CA-wide revocation and online
OCSP/CRL policy remain the responsibility of the deployment PKI and TLS terminus.

## Audit and recovery

`events` emits the append-only enrollment, activation, and revocation history.
The SQLite store uses WAL and full synchronous durability. Database and managed
credential directory must be backed up together. Missing files, an invalid
validity window, identity mismatch, key mismatch, duplicate fingerprint, or an
attempt to reactivate a revoked certificate fails closed.
