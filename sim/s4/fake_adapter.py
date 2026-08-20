#!/usr/bin/env python3
"""Deterministic local controller fixture for the protocol-only S4 checkpoint."""

import argparse
import asyncio
import json
import time


async def call(socket_path: str, method: str, params: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    writer.write(
        json.dumps(
            {"version": 1, "method": method, "params": params},
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


async def run(socket_path: str, expected: int, execution_seconds: float) -> None:
    completed = 0
    while completed < expected:
        response = await call(socket_path, "next_task", {})
        task = response.get("task")
        if task is None:
            await asyncio.sleep(0.02)
            continue
        await call(
            socket_path,
            "progress",
            {
                "task_id": task["task_id"],
                "progress_per_mille": 500,
                "stage": "fixture_executing",
            },
        )
        deadline = time.monotonic() + execution_seconds
        cancelled = False
        while time.monotonic() < deadline:
            status = await call(socket_path, "task_status", {"task_id": task["task_id"]})
            if status["state"] in {"cancel_pending", "cancelled"}:
                await call(
                    socket_path,
                    "complete",
                    {
                        "task_id": task["task_id"],
                        "outcome": "cancelled",
                        "error_code": "fixture.cancelled",
                    },
                )
                cancelled = True
                break
            if status["state"] in {"unknown", "failed", "rejected"}:
                cancelled = True
                break
            await asyncio.sleep(0.02)
        if cancelled:
            completed += 1
            continue
        await call(
            socket_path,
            "complete",
            {
                "task_id": task["task_id"],
                "outcome": "succeeded",
                "output": list(b'{"fixture":true}'),
            },
        )
        completed += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket")
    parser.add_argument("expected", type=int)
    parser.add_argument("--execution-seconds", type=float, default=0.0)
    args = parser.parse_args()
    asyncio.run(run(args.socket, args.expected, args.execution_seconds))


if __name__ == "__main__":
    main()
