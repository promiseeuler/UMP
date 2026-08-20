# Hardware Pilot Evidence

The PRD requires a supervised pilot with at least two physical robots and one
simulated participant. `ump-pilot` validates a portable evidence bundle for that
gate. It does not operate robots and cannot determine whether a physical review
was truthful or sufficient.

## Phases

`read_only` is the first integration phase. Every participant supplies a hashed
adapter conformance artifact. Physical adapters should advertise no invocable
capabilities while awareness, identity, freshness, and communication-loss
behavior are observed.

`supervised_assignment` is permitted structurally only when the bundle contains:

- at least two physical and one simulated participant;
- conformance evidence for every participant;
- supervised-assignment evidence for every physical participant;
- an adapter safety review;
- bounded-work-area evidence;
- a physical emergency-stop test record; and
- explicit operator approval.

These artifacts are deployment records, not UMP messages. Emergency stops remain
outside UMP and must be independently functional at the test site.

## Bundle

Use `schemas/ump-hardware-pilot-v1.schema.json` as the language-neutral format.
Artifact paths are relative to the manifest, cannot escape its directory, and
carry lowercase SHA-256 digests. Each gate requires a distinct artifact, and a
read-only bundle rejects assignment evidence. This makes the bundle portable and
detects missing, reused, contradictory, or modified evidence.

Validate it with:

```sh
ump-pilot validate pilot.json
ump-pilot schema
```

Successful output uses validation scope
`schema_topology_and_evidence_integrity`. It proves format, topology, phase
requirements, file containment, and digests only. Production readiness still
requires qualified humans to inspect the contents, sign the applicable reviews,
and accept residual safety and interoperability risk.

The artifact hashes do not authenticate the manifest itself. Deployments should
sign the final manifest with their approved document-signing system or retain it
in an access-controlled immutable evidence store.

Run the read-only phase first and retain its immutable bundle. Create a separate
pilot ID and bundle for supervised assignments so later evidence cannot rewrite
the earlier rollout record.
