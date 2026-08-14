# Phase 2 S2 Checkpoint Evidence

**Evidence class:** In-progress development checkpoint  
**Date:** 2026-08-14  
**Source state:** `d2d8fa0` plus uncommitted Phase 0 through Phase 2 work  
**Commands:** `make check`, `make sim-s0`, `make sim-s1`, `make sim-s2`

## Result

S2 deterministically exercises an authorized package-delivery task and the roadmap fault variants: cancellation before start and during execution, duplicate delivery, expired command, executor crash, network partition, lease expiry, lease revocation, coordinator restart, conflicting issuer, and a non-idempotent unknown outcome.

| Measurement | Result |
|---|---:|
| Scenario variants | 13 |
| Physical-action surrogates | 9 |
| Duplicate physical actions | 0 |
| Unauthorized executions | 0 |
| Sampled correlated trace events | 11 |
| S2 result | Passed |

The complete local regression also passed S0, S1, all Rust workspace tests, Clippy with warnings denied, formatting, and seven Python vector/handler tests.

## Superseded

This early checkpoint is superseded by [the Phase 2 exit report](./2026-08-14-s2.md).
