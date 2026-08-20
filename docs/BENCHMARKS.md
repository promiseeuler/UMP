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

Run the authenticated loopback transport profile separately:

```sh
ump-benchmark --profile tls-loopback --samples 1000
```

## Two-host LAN measurement

`ump-lan-benchmark` measures the same fresh mutual-TLS delivery path between two
separate hosts using deployment-issued credentials. On the receiving host:

```sh
ump-lan-benchmark server --robot-id benchmark-server --host 0.0.0.0 \
  --revision "$(git rev-parse HEAD)" \
  --port 7443 --certificate server.pem --private-key server.key --ca site-ca.pem \
  --samples 1000 --warmup-samples 64
```

After its JSON `ready` event, run on the sending host:

```sh
ump-lan-benchmark client --robot-id benchmark-client --host 192.0.2.10 \
  --revision "$(git rev-parse HEAD)" \
  --port 7443 --peer-id benchmark-server --certificate client.pem \
  --private-key client.key --ca site-ca.pem --samples 1000 --warmup-samples 64
```

The certificate URI identities must match `--robot-id` and `--peer-id`. Both
hosts must run and record the same full lowercase `--revision`. Operators should
also provide `--peer-certificate-sha256` when their deployment pins peer
certificates. Both roles emit machine-readable JSON; retain both reports with the
host models, operating systems, network topology, interface type, and run time.
A loopback run proves the harness only. The PRD healthy-LAN gate requires these
commands on separate representative hosts connected through the deployment LAN.

Save the client's JSON output as `client.json`. The server emits a `ready` JSON
event first; save only its final JSON line as `server.json`. Create a bundle
manifest using
`schemas/ump-lan-evidence-v1.schema.json`, record the SHA-256 digest of each
report, and set `repository_revision` to the full lowercase commit SHA of the
UMP checkout used on both hosts. Validate it with:

```sh
ump-lan-evidence validate lan-evidence.json
```

The verifier checks both artifact digests, report/manifest revision agreement,
successful benchmark gates, distinct client and server hostnames, non-loopback
addressing, reciprocal server identity and port, and matching sent/received
counts. It emits a machine-readable summary.
This proves evidence integrity and internal cross-host consistency; operators
must still verify that the recorded network description and machines represent
the intended deployment environment.

Each benchmark's final report is one JSON document. The process exits with `0`
when every configured gate passes, `1` when a measured gate fails, and `2` for
invalid arguments. The default in-memory run uses 5,000 measured state
publications and 100 idle participants.

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

The `ump.reference.tls-loopback/v1` profile measures one complete message and
acknowledgement per fresh TCP connection. Its timed path includes TLS 1.3 mutual
authentication, UMP ALPN, certificate robot-identity binding, canonical framing,
replay enforcement, handler execution, and acknowledgement validation. Ephemeral
certificate generation and context construction occur outside the timed region.

The runner performs 64 unmeasured warm-up publications before collecting
latency samples. It reports the Python implementation, version, executable, and
platform so results can be compared in context.

## Default gates

- In-memory reference propagation p95 must be no more than 100 ms.
- Incremental idle Python heap must be no more than 32 MiB per participant.
- Every observed canonical message must fit the 64 KiB core limit.
- TLS loopback round-trip p95 must be no more than 100 ms.

These gates catch reference-runtime regressions but are deliberately weaker
than production evidence. In-memory propagation does not prove the PRD's
healthy-LAN TLS target. Incremental Python heap does not include interpreter,
native library, kernel socket, or simulator memory. Production qualification
requires isolated-process RSS measurements and TLS tests across representative
hardware and network conditions.

## Recorded baseline

On 2026-08-20, `ump.reference.tls-loopback/v1` completed 1,000 samples on
CPython 3.14.2, macOS 26.4 arm64. Every sample opened a fresh mutual-TLS
connection. The 503-byte state message measured 1.6565 ms p50, 1.733792 ms p95,
1.8195 ms p99, and 600.35 sequential round trips per second. Both the 100 ms p95
gate and 64 KiB message-size gate passed.

This baseline proves the local implementation path on that environment. It does
not measure network latency, packet loss, concurrent peers, isolated-process RSS,
or a representative deployment LAN.
