# ADR 0005: Mandatory v1 transport profile

**Status:** Accepted provisionally for v1 prototype  
**Date:** 2026-08-14
**Owner:** UMP protocol and runtime maintainers

## Context

UMP needs one mandatory secure transport so independently developed machines can interoperate. Optional bindings may follow. The PRD initially favors QUIC, while Phase 0 requires two authenticated request/response candidates to be implemented and measured.

## Candidates

### Mutual TLS 1.3 over bounded TCP frames

Implemented. It has mature deployment behavior, simple stream semantics, mutual certificate authentication, and low implementation complexity. It needs explicit connection/channel design to prevent telemetry or bulk data from delaying control traffic.

### QUIC with mutual TLS 1.3

Implemented and measured under the same local profile. It offers independent streams, connection migration, and datagrams at a measured cost of higher clean-loopback latency and memory than TLS/TCP.

## Decision criteria

- Mutual identity and downgrade resistance.
- Control latency under clean and impaired networks.
- Reconnect and network-change behavior.
- Control isolation under telemetry load.
- Runtime memory, CPU, binary size, and ARM suitability.
- Implementation complexity and cross-language availability.
- Local discovery compatibility and operational diagnostics.

## Decision

Select QUIC with mutual TLS 1.3 and ALPN `ump/1` as the mandatory v1 prototype transport. Keep bounded mutual TLS/TCP as an optional bridge and diagnostic transport.

On the final Phase 0 Apple ARM64 loopback baseline, QUIC measured 49 us request/response p50, 525 us reconnect-plus-request p50, 13,952 KiB resident memory, and a 3,913,888-byte benchmark binary. TLS/TCP measured 29 us, 373 us, 4,448 KiB, and 2,980,880 bytes respectively. Both remain within provisional resource targets.

UMP needs independent streams for control, telemetry, and negotiated data so bulk or lost stream data does not block control traffic. That architectural property outweighs the modest clean-loopback overhead for the prototype.

## Reversal gate

Phase 1 MUST test QUIC under latency, jitter, loss, reordering, telemetry saturation, reconnect, and address change. The decision returns to proposed if QUIC cannot meet control latency/resource targets on native Linux ARM64 or lacks practical cross-language interoperability. No QUIC-specific behavior may leak into transport-independent UMP semantics.
