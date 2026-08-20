# S4 native performance profile

## Purpose

This profile makes resource evidence from the embodied S4 mission reproducible
and comparable. It applies unchanged to the reference and alternate mobile
models. A report is valid only when produced by the complete nominal mission on
native Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic.

## Commands and artifacts

Run `make ros2-s4` for the reference model and
`make ros2-s4-model-swap` for the alternate model. Each artifact directory
contains `performance.json`, `invariants.json`, `trace.jsonl`, inspector
snapshots, and logs.

The performance report records the source revision and dirty state, OS, kernel,
architecture, CPU, logical core count, host memory, container status, transport,
security mode, clock source, model variant, binary sizes, and per-runtime sample
statistics. CI must retain the report with the matching mission evidence.

## Sampling method

After stack startup and a five-second warmup, `performance.py sample` reads
`VmRSS` and process CPU ticks from Linux `/proc` every 100 ms for the mobile,
arm, and zone `umpd` processes. Sampling continues through task execution,
coordination, invariant verification, and inspector export. The report therefore
uses active-mission peak RSS, which is a stricter check than the PRD's idle
runtime target, and cumulative CPU seconds rather than normalized utilization.

## Acceptance gates

- The mobile, arm, and zone runtimes must each have at least two samples.
- Peak RSS must not exceed 64 MiB for any sampled runtime.
- The combined uncompressed `ump` and `umpd` executables must not exceed 25 MiB.
- The mission invariant report must pass.
- The report must identify the source and execution environment.

The executable-size gate is conservative for the two runtime binaries but is
not a measurement of a compressed distribution package or adapter dependencies.
Those package measurements remain a release gate.

## Current evidence boundary

Fixture tests cover report calculations and both acceptance outcomes. A Linux
container smoke covers live `/proc` sampling. Native Gazebo reports are not yet
published because Gazebo Transport cannot initialize multicast in the current
AMD64-on-Apple-Silicon QEMU environment. Command acknowledgement, emergency
propagation, discovery, and recovery latency require separate native benchmark
profiles and must not be inferred from this resource report.
