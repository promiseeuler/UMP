#!/usr/bin/env python3
"""Minimal loopback controller used to exercise the UMP HTTP bridge."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        expected = os.environ.get("UMP_EXAMPLE_CONTROLLER_TOKEN")
        if not expected or self.headers.get("Authorization") != f"Bearer {expected}":
            self.respond(401, {"status": "failed", "error_code": "controller.unauthorized"})
            return
        length = min(int(self.headers.get("Content-Length", "0")), 65_537)
        if length > 65_536:
            self.respond(413, {"status": "failed", "error_code": "controller.input_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.respond(400, {"status": "failed", "error_code": "controller.invalid_json"})
            return
        if self.path == "/v1/health":
            self.respond(200, {"status": "healthy"})
        elif self.path == "/v1/observations/current":
            self.respond(200, {
                "operational": "idle", "safety": "normal", "health_codes": []
            })
        elif self.path == "/v1/commands/execute":
            self.respond(200, {"status": "succeeded", "output": {"accepted": payload["task_id"]}})
        elif self.path == "/v1/commands/cancel":
            self.respond(200, {"status": "cancelled"})
        else:
            self.respond(404, {"status": "failed", "error_code": "controller.not_found"})

    def respond(self, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"controller: {format % args}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
