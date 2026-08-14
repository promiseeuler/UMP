# Native installation

UMP can run directly on a Linux robot computer as a Debian service or as a
rootless container. Neither path requires a cloud service or additional robot
hardware. The robot controller connects separately through the local adapter
socket in `/var/lib/ump/adapter.sock`.

For production downloads, verify the checksum, Sigstore signature, and GitHub
provenance before installation as described in the
[release verification guide](./release-verification.md).

## Debian package

Build and validate the package on Linux `amd64` or `arm64`:

```sh
make native-package-test
sudo dpkg -i dist/ump_0.1.0_$(dpkg --print-architecture).deb
```

The package installs `ump`, `umpd`, and `umpd.service`. It creates a locked
system account named `ump`; `/var/lib/ump` is private to that account. Initialize
the identity before starting the service:

```sh
sudo -u ump ump --data-dir /var/lib/ump init \
  --machine-id ump:machine:my-robot \
  --machine-class mobile_base \
  --listen 0.0.0.0:7443
sudo -u ump ump --data-dir /var/lib/ump doctor
sudo systemctl enable --now umpd.service
```

The default unit has filesystem, privilege, kernel, and home-directory
restrictions. It deliberately does not delete `/var/lib/ump` during uninstall;
that directory contains identity keys and audit journals. Export required audit
data and remove it explicitly when decommissioning a machine.

### Upgrade and rollback

Install a newer package with `sudo apt install ./ump_VERSION_ARCH.deb`. A rollback
uses the same command with an older package and apt's downgrade confirmation.
Stop `umpd` before either transition and restart it only after
`sudo -u ump ump --data-dir /var/lib/ump doctor` succeeds.

`make native-package-test` performs this lifecycle in a clean Ubuntu container.
It initializes a machine, runs the runtime, upgrades, rolls back, and removes the
package while proving the configuration and identity credentials remain byte-for-
byte unchanged. Uninstall retains `/var/lib/ump`; package purge is not a machine
decommissioning operation.

### Development credential rotation

Stop the runtime and confirm its exact machine ID when rotating locally generated
development credentials:

```sh
sudo systemctl stop umpd.service
sudo -u ump ump --data-dir /var/lib/ump rotate-development-credentials \
  --confirm-machine-id ump:machine:my-robot
sudo -u ump ump --data-dir /var/lib/ump doctor
```

The command validates the replacement root, certificate, and private key before
activation. It preserves configuration, capabilities, trust policy, journals,
and runtime state. The previous set is stored under
`/var/lib/ump/credential-archive/OLD_FINGERPRINT` with private directory and key
permissions for a controlled rollback window.

Rotation changes the certificate fingerprint. Every peer must revoke the old
fingerprint/root and enroll the new certificate before communication resumes.
The archive still contains usable secret material; remove it after the rollback
window according to the deployment's audit and secure-erasure policy. This
development command is not a substitute for a production PKI rotation service.

## Rootless container

Build the local image and create persistent state:

```sh
make container-build
docker volume create ump-state
docker run --rm --entrypoint /usr/local/bin/ump \
  -v ump-state:/var/lib/ump ump:local \
  --data-dir /var/lib/ump init \
  --machine-id ump:machine:my-robot \
  --machine-class mobile_base \
  --listen 0.0.0.0:7443
docker run -d --name umpd --restart unless-stopped \
  -p 7443:7443/udp -v ump-state:/var/lib/ump ump:local
```

The image runs as numeric user and group `65532`, contains no development
toolchain, and stores mutable state only in `/var/lib/ump`. Explicit peer
configuration works through the published UDP port. Host-network or deployment-
specific multicast configuration will be required when local discovery is
enabled in a later profile.

Development credentials created by `ump init` are only for local integration.
Production enrollment and credential rotation remain Phase 5 release gates.
