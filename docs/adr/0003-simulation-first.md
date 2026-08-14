# ADR 0003: Deterministic simulation first

**Status:** Accepted  
**Date:** 2026-08-14

## Decision

Implement protocol state machines against an injected monotonic clock and transport boundary. Validate each roadmap increment in deterministic simulation before physics or hardware testing.

## Rationale

Distributed failures must be reproducible. A deterministic kernel makes expiry, retries, partitions, duplicate delivery, and restart behavior testable in continuous integration.

## Consequences

Protocol code may not read wall-clock time directly. The S0 scenario is the first release gate; later network and Gazebo simulations reuse the runtime behavior.

