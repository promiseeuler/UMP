# ADR 0001: Rust reference runtime

**Status:** Accepted for prototype  
**Date:** 2026-08-14

## Decision

Build the reference runtime and deterministic simulation kernel in Rust. Expose language-neutral Protocol Buffers schemas and add Python and C++ SDKs in later roadmap phases.

## Rationale

Rust supports memory-safe network code, deterministic resource ownership, ARM64 cross-compilation, and compact standalone deployment. The protocol remains independent of this implementation choice.

## Consequences

The repository uses a Cargo workspace. Unsafe Rust is denied in core crates. Independent implementations are required before v1 interoperability claims.

