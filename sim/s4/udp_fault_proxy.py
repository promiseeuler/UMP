#!/usr/bin/env python3
"""Deterministic UDP relay for QUIC latency and packet-loss checkpoints."""

import argparse
import asyncio


class FaultRelay(asyncio.DatagramProtocol):
    def __init__(self, server, delay_ms, jitter_ms, drop_every, burst_every, burst_length):
        self.server = server
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self.drop_every = drop_every
        self.burst_every = burst_every
        self.burst_length = burst_length
        self.transport = None
        self.client = None
        self.client_packets = 0
        self.server_packets = 0

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        if address == self.server:
            if self.client is None:
                return
            self.server_packets += 1
            destination = self.client
            sequence = self.server_packets
        else:
            self.client = address
            self.client_packets += 1
            destination = self.server
            sequence = self.client_packets
        periodic_drop = self.drop_every > 0 and sequence % self.drop_every == 0
        burst_drop = (
            self.burst_every > 0
            and (sequence - 1) % self.burst_every < self.burst_length
        )
        if periodic_drop or burst_drop:
            return
        jitter = ((sequence % 3) - 1) * self.jitter_ms
        delay_seconds = max(0, self.delay_ms + jitter) / 1000
        asyncio.get_running_loop().call_later(
            delay_seconds, self.transport.sendto, data, destination
        )


async def run(args) -> None:
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: FaultRelay(
            ("127.0.0.1", args.server_port),
            args.delay_ms,
            args.jitter_ms,
            args.drop_every,
            args.burst_every,
            args.burst_length,
        ),
        local_addr=("127.0.0.1", args.listen_port),
    )
    await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--drop-every", type=int, default=0)
    parser.add_argument("--burst-every", type=int, default=0)
    parser.add_argument("--burst-length", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
