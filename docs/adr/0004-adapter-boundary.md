# ADR 0004: Out-of-process adapter boundary

**Status:** Accepted for prototype  
**Date:** 2026-08-14
**Owner:** UMP safety and runtime maintainers

## Decision

Treat the UMP runtime and machine adapter as separate logical components with an authenticated, bounded local interface. Early mock adapters may run in-process for deterministic simulation, but physical adapters default to a separate process or separately supervised ROS 2 node.

## Rationale

Adapters contain vendor code and physical-effect mappings. Isolation limits crashes and dependency conflicts, makes authority checks observable, and allows the runtime to detect adapter loss and trigger declared safe behavior.

## Consequences

Every physical adapter publishes the adapter safety contract. The local adapter API must carry machine identity, capability scope, deadlines, correlation, and explicit disconnect behavior. In-process deployment requires a profile-specific justification.
