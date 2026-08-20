# Independent Review Evidence

UMP production qualification requires independent security, safety, and
interoperability reviews. `ump-review` validates a common portable evidence
format; it does not perform a review or certify that a reviewer is qualified.

## Scope

Each review manifest binds the review to one 40-character repository revision
and records:

- review type and bounded scope;
- reviewer name, organization, independence assertion, and conflict disclosure;
- a hashed review report and separate independence attestation;
- structured findings with severity and status; and
- an explicit conclusion.

Use `schemas/ump-independent-review-v1.schema.json` as the language-neutral
format. All artifact paths are relative to the manifest, remain inside the
bundle, and carry lowercase SHA-256 digests.

```sh
ump-review validate independent-review.json
ump-review schema
```

Exit status `0` means the bundle is valid and its conclusion is `approved` or
`approved_with_conditions`. Exit status `1` means the evidence is valid but the
review conclusion is `rejected`. Exit status `2` means the evidence cannot be
trusted structurally or an artifact failed integrity validation.

## Findings

Open critical or high findings prevent validation. An `approved` review cannot
contain any open finding. Every accepted finding requires a distinct disposition
artifact recording the responsible risk owner and decision. Resolved findings
should be backed by the main report or supporting evidence.

The verifier checks the declared independence field and attestation digest, but
cannot establish a person's identity, competence, or actual independence.
Release owners must verify those facts through their procurement, accreditation,
and document-signing processes. Sign or immutably retain the final manifest;
hashes protect referenced artifacts but do not authenticate the manifest itself.

One review bundle covers one review type. Security, safety, and interoperability
qualification therefore require three separately scoped, independently retained
results for the exact release revision.
