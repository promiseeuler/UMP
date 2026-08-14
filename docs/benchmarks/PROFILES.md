# UMP Benchmark Profiles

Benchmark reports MUST identify enough context to be reproduced. Results without this metadata are exploratory and cannot satisfy a roadmap gate.

## Required metadata

- Source revision and dirty-worktree state.
- Rust/compiler and dependency lockfile versions.
- OS, kernel, CPU architecture, CPU model, logical cores, and memory.
- Power mode and virtualization/container status.
- Transport, address family, loopback or physical network, and MTU where relevant.
- Authentication and encryption mode.
- Message body and encoded frame sizes.
- Warmup count, measured iterations, and concurrency.
- Clock source and resolution.

## Reference profiles

### `dev-arm64-macos`

Local development evidence only. Apple ARM64, loopback network, release build, one client and one server. Useful for regressions but not a Linux support claim.

### `linux-arm64-reference`

Native Linux ARM64 onboard-class computer with at least four cores and 4 GB RAM. Exact hardware is recorded in each report.

### `linux-amd64-reference`

Native Linux AMD64 machine with at least four cores and 8 GB RAM. Exact hardware is recorded in each report.

## Phase 0 measurements

- Release binary bytes on disk.
- Process startup to listening/ready.
- Idle resident memory after warmup.
- Authenticated connection setup latency.
- Protobuf request/response p50, p95, and p99 latency.
- Reconnect latency after an orderly close and forced disconnect.
- Success/failure counts and total duration.

At least 1,000 request/response samples follow 100 warmups for latency reports. Benchmarks MUST cap message sizes and MUST fail if authentication is disabled.

