# Phase 0 TLS/TCP Baseline: macOS ARM64

**Evidence class:** Exploratory development baseline  
**Date:** 2026-08-14  
**Source revision:** `d2d8fa0` plus uncommitted Phase 0 transport work  
**Command:** `make benchmark-phase0`

This report does not satisfy Linux support or clean-source release evidence. It validates the benchmark harness and establishes a local regression baseline.

## Environment

| Property | Value |
|---|---|
| Architecture | Apple ARM64 (`aarch64`) |
| Operating system | Darwin 25.4.0 |
| Rust | 1.85.0 |
| Build | Cargo release profile |
| Network | TCP/IPv4 loopback |
| Security | TLS 1.3, mutual development-CA leaf certificates |
| Frame | 77 bytes including four-byte length prefix |
| Warmup | 100 requests |
| Measured | 1,000 requests and 100 reconnects |

## Results

| Measurement | Result |
|---|---:|
| Startup through credential creation and listening | 1,181 us |
| Initial authenticated connection | 1,290 us |
| Request/response p50 | 29 us |
| Request/response p95 | 72 us |
| Request/response p99 | 93 us |
| Request/response maximum | 139 us |
| Reconnect plus request/response p50 | 373 us |
| Reconnect plus request/response p95 | 550 us |
| Reconnect plus request/response p99 | 682 us |
| Release benchmark binary | 2,980,880 bytes |
| Resident memory at report time | 4,448 KiB |

## Interpretation

The prototype is well below the provisional 64 MB runtime memory and 25 MB compressed-package goals on this development profile. Loopback latency is not representative of Wi-Fi, Ethernet switching, packet loss, or onboard CPU contention. The result supports continued TLS/TCP evaluation but does not choose it as UMP's mandatory transport.

The benchmark must be repeated from a clean revision on native Linux ARM64 and AMD64. A second authenticated candidate must be measured under the same message and sampling profile before ADR 0005 can move from proposed to accepted.
