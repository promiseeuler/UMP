# Phase 0 QUIC Baseline: macOS ARM64

**Evidence class:** Exploratory development baseline  
**Date:** 2026-08-14  
**Source revision:** `d2d8fa0` plus uncommitted Phase 0 transport work  
**Command:** `cargo run --release -p ump-transport --bin phase0-bench-quic`

This report validates the second candidate and benchmark harness. It is not Linux support or clean-source release evidence.

## Environment

| Property | Value |
|---|---|
| Architecture | Apple ARM64 (`aarch64`) |
| Operating system | Darwin 25.4.0 |
| Rust | 1.85.0 |
| Build | Cargo release profile |
| Network | QUIC over UDP/IPv4 loopback |
| Security | TLS 1.3, mutual development-CA leaf certificates |
| Frame | 77 bytes including four-byte length prefix |
| Warmup | 100 requests |
| Measured | 1,000 requests and 100 reconnects |

## Results

| Measurement | Result |
|---|---:|
| Startup through credential creation and listening | 1,388 us |
| Initial authenticated connection | 1,572 us |
| Request/response p50 | 49 us |
| Request/response p95 | 99 us |
| Request/response p99 | 125 us |
| Request/response maximum | 192 us |
| Reconnect plus request/response p50 | 525 us |
| Reconnect plus request/response p95 | 663 us |
| Reconnect plus request/response p99 | 740 us |
| Release benchmark binary | 3,913,888 bytes |
| Resident memory at report time | 13,952 KiB |

## Comparison and interpretation

QUIC is 20 us slower at request/response p50 and 152 us slower for reconnect-plus-request p50 than the TLS/TCP candidate on this clean loopback run. Its measured memory and binary costs remain below provisional UMP budgets.

The clean single-stream benchmark does not measure QUIC's principal benefits: independent streams, datagrams, transport-level loss recovery, and connection migration. UMP's control, telemetry, and data planes need isolation from stream head-of-line blocking, so QUIC is selected provisionally despite its modest local overhead. Phase 1 network-fault and load tests are a mandatory validation gate.
