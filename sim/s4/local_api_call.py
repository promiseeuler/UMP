#!/usr/bin/env python3
"""Call one method on the newline-delimited local adapter API."""

import argparse
import asyncio
import json

from fake_adapter import call


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket")
    parser.add_argument("method")
    parser.add_argument("params_json")
    args = parser.parse_args()
    result = asyncio.run(call(args.socket, args.method, json.loads(args.params_json)))
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
