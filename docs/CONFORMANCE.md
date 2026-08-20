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

Manufacturers can generate a retainable read-only report without writing Python:

```sh
ump-adapter-conformance inspect \
  --adapter acme_ump.adapter:create_adapter \
  --adapter-config /etc/acme/ump-adapter.json \
  --revision "$(git rev-parse HEAD)" \
  --output adapter-conformance.json
```

`--revision` must be the full lowercase 40-character commit SHA of the UMP
checkout whose adapter contract is being tested. The retained report binds that
revision to the adapter implementation digest, package versions, robot metadata,
and observed environment.

The command records the loaded factory specification, implementation-file
SHA-256, Python distribution versions when available, robot and capability
metadata, environment, and all read-only checks. It prints the same JSON written
to `--output`. Exit status `0` means all checks pass, `1` means a report was
produced with failed checks, and `2` means the adapter or report could not be
loaded or generated.

Review retained evidence independently, optionally rebinding it to the exact
implementation file supplied by the manufacturer:

```sh
ump-adapter-conformance verify adapter-conformance.json \
  --implementation acme_ump/adapter.py
ump-adapter-conformance schema
```

Verification requires the exact three read-only checks, rejects duplicate or
contradictory summaries, and recomputes the source digest when
`--implementation` is supplied. Use
`schemas/ump-adapter-conformance-v1.schema.json` from non-Python tooling.

Adapter factories are trusted local code and may initialize a vendor SDK merely
by being loaded. Run this command only with reviewed manufacturer packages. The
CLI deliberately exposes no native-execution switch. Retain the report in a
signed or immutable evidence store because its implementation digest does not
authenticate the report file itself.

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
