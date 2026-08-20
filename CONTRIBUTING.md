# Contributing to UMP

UMP changes must preserve the boundary between manufacturer-neutral coordination
and native robot control. Read `docs/PRD.md`, `docs/PROTOCOL.md`, and the relevant
profile before changing behavior.

## Development checks

Use Python 3.11 or newer and install the project in editable mode:

```sh
python -m pip install -e .
python -m unittest discover -s tests
ruff check src tests
ump-conformance conformance/v0.1
ump-readiness
```

Protocol changes require schema updates, byte-exact conformance vectors, tests,
and documentation in the same pull request. New PRD requirement identifiers must
also be represented in `compliance/requirements.json`.

Never commit private keys, issued robot certificates, runtime databases, owner
telemetry, or simulator-generated build artifacts. Native capability execution
tests must use explicit operator-controlled fixtures and must not run against
physical hardware by default.

Keep changes focused, typed, deterministic, and compatible with the documented
wire version. Report security issues through the process in `SECURITY.md`.
