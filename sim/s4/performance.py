#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import signal
import time


def process_sample(pid: int) -> dict | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rss_kib = 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss_kib = int(line.split()[1])
            break
    fields = stat.rsplit(") ", 1)[1].split()
    return {
        "rss_kib": rss_kib,
        "cpu_ticks": int(fields[11]) + int(fields[12]),
    }


def sample(output: Path, processes: dict[str, int], interval_seconds: float) -> None:
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as stream:
        while running:
            record = {"monotonic_ns": time.monotonic_ns(), "processes": {}}
            for name, pid in processes.items():
                observed = process_sample(pid)
                if observed is not None:
                    record["processes"][name] = observed
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            stream.flush()
            time.sleep(interval_seconds)


def first_line(path: Path, prefix: str) -> str:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(prefix):
                return line.split(":", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "unknown"


def report(samples_path: Path, invariants_path: Path, output: Path) -> dict:
    samples = [json.loads(line) for line in samples_path.read_text().splitlines() if line]
    if not samples:
        raise ValueError("performance sample set is empty")
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    process_names = sorted(
        {name for item in samples for name in item["processes"]}
    )
    processes = {}
    for name in process_names:
        observations = [
            item["processes"][name]
            for item in samples
            if name in item["processes"]
        ]
        ticks = [item["cpu_ticks"] for item in observations]
        processes[name] = {
            "sample_count": len(observations),
            "peak_rss_kib": max(item["rss_kib"] for item in observations),
            "cpu_seconds": round((max(ticks) - min(ticks)) / ticks_per_second, 6),
        }

    invariants = json.loads(invariants_path.read_text())
    binary_directory = Path(os.environ.get("UMP_BINARY_DIR", "/usr/local/bin"))
    binary_sizes = {
        name: (binary_directory / name).stat().st_size for name in ("ump", "umpd")
    }
    duration_seconds = round(
        (samples[-1]["monotonic_ns"] - samples[0]["monotonic_ns"]) / 1_000_000_000,
        6,
    )
    memory_value = first_line(Path("/proc/meminfo"), "MemTotal").split()[0]
    memory_kib = int(memory_value) if memory_value.isdigit() else None
    checks = [
        {
            "name": "runtime_peak_rss_below_64_mib",
            "passed": all(value["peak_rss_kib"] <= 64 * 1024 for value in processes.values()),
            "limit_kib": 64 * 1024,
        },
        {
            "name": "combined_runtime_cli_below_25_mib",
            "passed": sum(binary_sizes.values()) <= 25 * 1024 * 1024,
            "limit_bytes": 25 * 1024 * 1024,
        },
        {
            "name": "mission_invariants_passed",
            "passed": invariants.get("passed") is True,
        },
        {
            "name": "all_runtimes_sampled",
            "passed": set(processes) == {"arm", "mobile", "zone"}
            and all(value["sample_count"] >= 2 for value in processes.values()),
        },
    ]
    failed = [item["name"] for item in checks if not item["passed"]]
    result = {
        "profile": "s4-native-loopback",
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "mission": {
            "duration_seconds": duration_seconds,
            "task_count": invariants.get("task_count"),
            "trace_record_count": invariants.get("record_count"),
            "mobile_model_variant": os.environ.get(
                "UMP_MOBILE_MODEL_VARIANT", "reference"
            ),
        },
        "processes": processes,
        "binary_sizes_bytes": binary_sizes,
        "environment": {
            "source_revision": os.environ.get("UMP_SOURCE_REVISION", "unknown"),
            "dirty_worktree": os.environ.get("UMP_SOURCE_DIRTY", "unknown"),
            "os": platform.platform(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "cpu_model": first_line(Path("/proc/cpuinfo"), "model name"),
            "logical_cores": os.cpu_count(),
            "memory_kib": memory_kib,
            "containerized": Path("/.dockerenv").exists(),
            "transport": "mutual-TLS QUIC over IPv4 loopback",
            "ros_transport": "DDS and Gazebo Transport on container-local network",
            "security": "development PKI with mutual authentication",
            "clock": "CLOCK_MONOTONIC via time.monotonic_ns",
            "sample_interval_ms": 100,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sampler = subparsers.add_parser("sample")
    sampler.add_argument("--output", type=Path, required=True)
    sampler.add_argument("--process", action="append", required=True)
    sampler.add_argument("--interval-ms", type=int, default=100)
    reporter = subparsers.add_parser("report")
    reporter.add_argument("--samples", type=Path, required=True)
    reporter.add_argument("--invariants", type=Path, required=True)
    reporter.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "sample":
        processes = {}
        for value in args.process:
            name, raw_pid = value.split("=", 1)
            processes[name] = int(raw_pid)
        sample(args.output, processes, args.interval_ms / 1000)
        return
    result = report(args.samples, args.invariants, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
