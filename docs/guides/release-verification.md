# Release verification

Official UMP releases are created only from version tags matching the workspace
version. The release workflow tests the workspace, builds and exercises the
Debian lifecycle independently on Linux `amd64` and `arm64`, creates standalone
bundles, and publishes one multi-architecture rootless image.

Every Debian package and standalone bundle has:

- a SHA-256 entry in the release-level `SHA256SUMS` file;
- a keyless Sigstore bundle produced with the release workflow's GitHub OIDC
  identity; and
- a GitHub build-provenance attestation bound to the artifact digest.

The OCI manifest is signed by the same workflow. BuildKit also publishes
maximal provenance and an SBOM for the multi-architecture image.

The `ump-ros2-jazzy` package is built and install-tested separately for Linux
`amd64` and `arm64`, then receives the same Sigstore signature and GitHub
provenance treatment as the native Debian package.

## Verify downloaded files

Install `cosign` and the GitHub CLI, download one release and verify it:

```sh
gh release download v0.1.0 --repo promiseeuler/UMP --dir ump-release
cd ump-release
sha256sum -c SHA256SUMS

cosign verify-blob \
  --bundle ump_0.1.0_linux_arm64.tar.gz.sigstore.json \
  --certificate-identity-regexp \
    '^https://github.com/promiseeuler/UMP/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ump_0.1.0_linux_arm64.tar.gz

gh attestation verify ump_0.1.0_linux_arm64.tar.gz \
  --repo promiseeuler/UMP
```

Repeat signature and provenance verification for the selected Debian package.
Verify `SHA256SUMS` itself with its adjacent Sigstore bundle before treating it
as an authority for the other files.

## Verify the container

Resolve and verify the immutable digest rather than trusting a mutable tag:

```sh
image=ghcr.io/promiseeuler/ump
digest=$(docker buildx imagetools inspect "$image:0.1.0" \
  --format '{{json .Manifest.Digest}}' | tr -d '"')
cosign verify \
  --certificate-identity-regexp \
    '^https://github.com/promiseeuler/UMP/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "$image@$digest"
```

Release automation is configured in the repository, but no artifact should be
described as officially published or signed until the tag workflow completes
and these commands verify its uploaded evidence.
