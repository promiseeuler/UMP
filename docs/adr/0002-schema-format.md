# ADR 0002: Protocol Buffers schemas

**Status:** Accepted for prototype  
**Date:** 2026-08-14
**Owner:** UMP protocol editors

## Decision

Use Protocol Buffers as the reference wire schema and code-generation source. Vendor the compiler in the Rust build so a system `protoc` installation is not required.

## Rationale

The format is compact, language neutral, and has explicit field-number compatibility rules. Canonical JSON is reserved for diagnostics and tooling.

## Consequences

Field numbers are permanent and may never be reused. Semantics remain specified outside generated code. Transport bindings may not alter message meaning.
