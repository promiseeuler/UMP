# Conformance

UMP ships versioned golden vectors and an adapter harness so implementations can
be checked without granting robot control authority.

## Wire vectors

Run the `ump/0.1` vector suite from a source checkout:

```sh
PYTHONPATH=src python3 -m ump.cli conformance conformance/v0.1
```

An installed package exposes the equivalent command:

```sh
ump-conformance conformance/v0.1
```

The suite manifest names the protocol schema, expected result, encoding, and
SHA-256 digest for every vector. Vector files contain hexadecimal bytes so line
ending or editor normalization cannot alter the wire input. A valid vector must
decode, satisfy the JSON Schema, and re-encode to the exact same canonical bytes.
Invalid vectors must be rejected.

The command emits one JSON report and returns `0` when every check passes, `1`
for a conformance failure, and `2` when the suite cannot be loaded.

## Adapter harness

`AdapterConformanceHarness.inspect(adapter)` only reads the adapter manifest and
state. It checks their model types, robot identity agreement, and each advertised
capability's input and output schemas.

Native capability execution is a separate operation:

```python
report = harness.exercise(
    adapter,
    assignments,
    allow_native_execution=True,
)
```

Without the explicit `allow_native_execution=True` argument, `exercise` raises
`PermissionError`. The gate does not replace robot-local UMP authority leases,
native safety controls, supervision, or a controlled test environment.

## Scope

Passing these checks demonstrates compatibility with the checked-in `ump/0.1`
contract. It is not certification of physical safety, timing performance,
network resilience, or manufacturer-specific behavior.
