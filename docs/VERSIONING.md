# Versioning and Release Artifacts

UMP uses semantic versioning for the reference package. Before `1.0.0`, minor
versions may change unstable Python APIs, but the wire protocol version changes
only through the explicit compatibility process in `PROTOCOL.md`. Patch releases
must remain wire compatible with their minor line.

Release tags use `vMAJOR.MINOR.PATCH` and must match the version in
`pyproject.toml`. Pushing a version tag or manually dispatching the release
workflow builds both a wheel and source distribution, validates package metadata,
installs the wheel into an isolated environment, runs the demo and both benchmark
profiles outside the source checkout, and uploads SHA-256 checksums.

The wheel contains the dependency-light Python runtime and inspector assets. The
source distribution additionally contains the PRD and profiles, JSON Schema,
conformance vectors, compliance matrix, tests, examples, and ROS 2 workspace so
manufacturers can audit and build the complete reference project from one archive.

The workflow does not publish to PyPI or create a GitHub Release automatically.
Those are explicit maintainer decisions after CI, changelog, compatibility, and
safety-status review. Public repositories additionally receive GitHub build
provenance attestations through `actions/attest`; private repositories require an
eligible GitHub plan for that service.

Verify downloaded files against `SHA256SUMS`. When a provenance attestation is
available, verify it with GitHub CLI against this repository before installation.
Release artifacts do not certify UMP or an adapter for physical robot operation.

## Retained qualification evidence

The production release gate requires a retained bundle containing the exact
wheel, source distribution, `SHA256SUMS`, isolated-install report, and a GitHub
attestation verification receipt. Validate the bundle with:

```sh
ump-release-evidence validate release-evidence.json
ump-release-evidence schema
```

The manifest binds every artifact by relative path, byte size, and SHA-256. Its
tag must match the package version, and the install and provenance receipts must
agree on the release ID, repository, and full commit revision. The install report
uses profile `ump.release-install-report/v1` and records passing
`wheel_installed`, `import_smoke`, `demo`, and `schema_commands` checks.
The release workflow generates and retains this report after every isolated
wheel smoke sequence.

The provenance receipt uses profile
`ump.github-attestation-verification/v1`, records the repository and revision,
and binds both distribution filenames and digests after successful `gh
attestation verify` commands. The UMP verifier checks retention and consistency;
it does not replace GitHub CLI's cryptographic verification of the attestation.
