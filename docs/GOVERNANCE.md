# UMP Governance

**Status:** Phase 0 draft

UMP uses an open, specification-led process. The repository maintainers steward releases and conformance language; they do not own vendor extensions or implementations.

## Roles

- **Contributor:** proposes code, documentation, tests, or review.
- **Maintainer:** reviews changes and owns one or more project areas.
- **Protocol editor:** ensures normative language, schemas, compatibility, and conformance agree.
- **Security responder:** receives and coordinates private vulnerability reports.
- **Release manager:** assembles and verifies release evidence.

Initial named role assignments remain open until additional maintainers join. Repository owners serve provisionally and MUST record final assignments before Phase 0 closes.

## Decisions

Routine compatible changes use pull-request consensus. Normative behavior, wire compatibility, conformance, governance, and security-boundary changes require a UEP. A UEP must include alternatives, compatibility, security/safety impact, and executable verification.

Maintainers document unresolved objections. Safety or security objections block release until they are resolved, explicitly accepted with rationale, or moved outside the release scope without weakening a published claim.

## Releases

Release managers publish specification and schema versions, source revision, artifacts, conformance results, simulation evidence, benchmark environment, compatibility matrix, security summary, known limitations, and upgrade/rollback instructions.

No implementation may use an official conformance mark until the corresponding public test profile exists and passes. Trademark and third-party certification policy remain pre-v1 decisions.

