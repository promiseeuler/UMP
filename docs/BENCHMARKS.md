# Benchmarks

UMP includes a reproducible quality harness for the dependency-light reference
runtime:

```sh
ump-benchmark
```

From a source checkout, run:

```sh
PYTHONPATH=src python3 -m ump.cli benchmark
```

The command emits one JSON document and exits with `0` when every configured
gate passes, `1` when a measured gate fails, and `2` for invalid arguments. The
default run uses 5,000 measured state publications and 100 idle participants.

## Measurements

- `propagation_p50_ms`, `propagation_p95_ms`, and `propagation_p99_ms` measure a
  synchronous state publication through canonical JSON encoding/decoding and
  registry update on `ump.reference.in-memory/v1`.
- `propagation_messages_per_second` is computed from the sum of individual
  measured publication durations.
- `idle_heap_per_participant_bytes` is incremental Python heap retained while
  constructing participants, adapters, subscriptions, manifests, states, and
  registry views. It is measured with `tracemalloc` and divided by participant
  count.
- `largest_observed_message_bytes` is the largest canonical envelope generated
  during the run and is checked against the 64 KiB core-profile limit.

The runner performs 64 unmeasured warm-up publications before collecting
latency samples. It reports the Python implementation, version, executable, and
platform so results can be compared in context.

## Default gates

- In-memory reference propagation p95 must be no more than 100 ms.
- Incremental idle Python heap must be no more than 32 MiB per participant.
- Every observed canonical message must fit the 64 KiB core limit.

These gates catch reference-runtime regressions but are deliberately weaker
than production evidence. In-memory propagation does not prove the PRD's
healthy-LAN TLS target. Incremental Python heap does not include interpreter,
native library, kernel socket, or simulator memory. Production qualification
requires isolated-process RSS measurements and TLS tests across representative
hardware and network conditions.
