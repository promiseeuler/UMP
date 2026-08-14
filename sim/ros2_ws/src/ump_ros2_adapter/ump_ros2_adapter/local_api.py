"""Newline-delimited JSON client for the bounded local UMP adapter API."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class LocalApiClient:
    def __init__(self, socket_path: str, timeout_seconds: float = 1.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.socket_path), self.timeout_seconds
        )
        request = {"version": 1, "method": method, "params": params}
        writer.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
        try:
            line = await asyncio.wait_for(reader.readline(), self.timeout_seconds)
            if not line:
                raise ConnectionError("UMP local API closed without a response")
            response = json.loads(line)
            if response.get("error"):
                raise RuntimeError(str(response["error"]))
            return response.get("result", {})
        finally:
            writer.close()
            await writer.wait_closed()

