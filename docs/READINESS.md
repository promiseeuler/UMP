# Requirement Traceability

`compliance/requirements.json` maps every named functional requirement in the
PRD to an explicit `implemented`, `partial`, or `missing` status. A separate
`compliance/qualification.json` tracks production evidence gates. This prevents
complete code traceability from being mistaken for permission to deploy on
physical robots.

Run the current readiness assessment with:

```sh
ump-readiness
```

The report distinguishes `functional_ready`, `production_ready`, and overall
`ready`. Exit status `0` means both functional requirements and all production
qualification gates pass. Exit status `1` means the matrices are valid but the
product is not fully qualified. Exit status `2` means a matrix cannot be trusted.

Evidence paths must remain inside the project root. For every gate marked
`passed`, readiness invokes the gate's authoritative domain verifier rather than
trusting file existence. It validates native ROS 2 outcomes and world binding,
two-host LAN bundle integrity, supervised hardware-pilot topology, passing
robot-bound adapter conformance, review type and conclusion, and retained
release artifact integrity. Validation summaries are emitted as
`validated_results` on each gate.

The production gates cover native ROS 2/Gazebo evidence, representative
two-host LAN measurements, the supervised hardware pilot, independent adapter
conformance, security review, safety review, interoperability review, and a
retained tagged release artifact. A gate cannot be marked `passed` without at
least one existing result-evidence file that passes the gate-specific verifier.

Release evidence is checked with `ump-release-evidence`; this validates retained
artifact and receipt consistency while leaving cryptographic provenance
verification to `gh attestation verify` as documented in `VERSIONING.md`.

To check functional implementation alone without claiming production readiness:

```sh
ump-readiness --functional-only
```

CI uses structural validation while unfinished product work remains visible:

```sh
ump-readiness --validate-only
```

Validation-only mode does not claim product readiness. It returns success only
for matrix integrity and still emits all readiness values and status counts.
Requirement and gate statuses should change only with direct, retained evidence,
never because adjacent functionality appears similar.
