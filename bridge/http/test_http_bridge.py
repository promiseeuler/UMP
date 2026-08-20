import asyncio
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MODULE_PATH = Path(__file__).with_name("ump_http_bridge.py")
SPEC = importlib.util.spec_from_file_location("ump_http_bridge", MODULE_PATH)
bridge_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = bridge_module
SPEC.loader.exec_module(bridge_module)


class ControllerHandler(BaseHTTPRequestHandler):
    requests = []
    cancelled = threading.Event()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.requests.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/execute":
            self.respond(200, {"status": "succeeded", "output": {"accepted": True}})
        elif self.path == "/fail":
            self.respond(503, {"error": "offline"})
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_url)
            self.end_headers()
        elif self.path == "/slow":
            self.__class__.cancelled.wait(2)
            self.respond(200, {"status": "failed", "error_code": "controller.stopped"})
        elif self.path == "/cancel":
            self.__class__.cancelled.set()
            self.respond(200, {"status": "cancelled"})
        else:
            self.respond(404, {})

    def respond(self, status, value):
        encoded = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        pass


@contextmanager
def server(handler=ControllerHandler):
    isolated_handler = type(
        "IsolatedControllerHandler",
        (handler,),
        {"requests": [], "cancelled": threading.Event()},
    )
    instance = ThreadingHTTPServer(("127.0.0.1", 0), isolated_handler)
    instance.redirect_url = ""
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


class FakeApi:
    def __init__(self, task=None, states=None):
        self.task = task
        self.states = iter(states or ["running"])
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "next_task":
            task, self.task = self.task, None
            return {"task": task}
        if method == "task_status":
            return {"state": next(self.states, "running")}
        return {}


def task(capability="example.execute", interruptible=True, deadline_ms=None):
    payload = json.dumps({"position": 7}).encode()
    return {
        "task_id": "task-1",
        "capability": capability,
        "input": list(payload),
        "input_content_type": "application/json",
        "deadline_ms": deadline_ms or int(time.time() * 1000) + 10_000,
        "correlation_id": "corr-1",
        "interruptible": interruptible,
    }


def config(base_url, path="/execute", cancel_path="/cancel", read_only=False):
    route = bridge_module.CapabilityRoute(path, cancel_path, 1.0, 4096)
    return bridge_module.BridgeConfig(
        "/tmp/ump.sock", base_url, "UMP_BRIDGE_TEST_TOKEN", None, None, None,
        True, read_only, 0.01, {"example.execute": route}
    )


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["UMP_BRIDGE_TEST_TOKEN"] = "secret"

    def tearDown(self):
        os.environ.pop("UMP_BRIDGE_TEST_TOKEN", None)

    async def test_success_maps_task_and_result(self):
        with server() as http:
            cfg = config(f"http://127.0.0.1:{http.server_port}")
            api = FakeApi(task())
            handled = await bridge_module.HttpBridge(
                cfg, api, bridge_module.HttpController(cfg)
            ).run_once()
        self.assertTrue(handled)
        self.assertEqual(http.RequestHandlerClass.requests[0][1], "Bearer secret")
        sent = json.loads(http.RequestHandlerClass.requests[0][2])
        self.assertEqual(sent["input"], {"position": 7})
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["outcome"], "succeeded")

    async def test_controller_failure_is_retryable(self):
        with server() as http:
            cfg = config(f"http://127.0.0.1:{http.server_port}", path="/fail")
            api = FakeApi(task())
            await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["error_code"], "bridge.controller_unavailable")
        self.assertTrue(completion["retryable"])

    async def test_cancel_reaches_controller(self):
        with server() as http:
            cfg = config(f"http://127.0.0.1:{http.server_port}", path="/slow")
            api = FakeApi(task(), ["cancel_pending"])
            await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
            self.assertTrue(http.RequestHandlerClass.cancelled.wait(1))
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["outcome"], "cancelled")

    async def test_read_only_never_claims_task(self):
        cfg = config("http://127.0.0.1:1", read_only=True)
        api = FakeApi(task())
        handled = await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
        self.assertFalse(handled)
        self.assertEqual(api.calls, [("ping", {})])

    async def test_interruptible_task_requires_cancel_route(self):
        cfg = config("http://127.0.0.1:1", cancel_path=None)
        api = FakeApi(task())
        await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["error_code"], "bridge.cancellation_unsupported")

    async def test_expired_task_is_not_dispatched(self):
        cfg = config("http://127.0.0.1:1")
        api = FakeApi(task(deadline_ms=1))
        await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["error_code"], "bridge.deadline_exceeded")

    async def test_redirect_is_rejected_without_forwarding_token(self):
        with server() as destination, server() as origin:
            origin.redirect_url = f"http://127.0.0.1:{destination.server_port}/execute"
            cfg = config(f"http://127.0.0.1:{origin.server_port}", path="/redirect")
            api = FakeApi(task())
            await bridge_module.HttpBridge(cfg, api, bridge_module.HttpController(cfg)).run_once()
            destination_hits = list(destination.RequestHandlerClass.requests)
        self.assertEqual(destination_hits, [])
        completion = [params for method, params in api.calls if method == "complete"][0]
        self.assertEqual(completion["error_code"], "bridge.controller_rejected")


class ConfigTests(unittest.TestCase):
    def write_config(self, value):
        temporary = tempfile.NamedTemporaryFile(mode="w", delete=False)
        json.dump(value, temporary)
        temporary.close()
        self.addCleanup(lambda: os.unlink(temporary.name))
        return Path(temporary.name)

    def test_rejects_remote_plaintext_and_unknown_secret_field(self):
        base = {"ump_socket": "/tmp/a", "controller_base_url": "http://example.com", "capabilities": {}}
        with self.assertRaisesRegex(bridge_module.BridgeError, "HTTPS"):
            bridge_module.BridgeConfig.load(self.write_config(base))
        base["controller_base_url"] = "https://example.com"
        base["token"] = "secret"
        with self.assertRaisesRegex(bridge_module.BridgeError, "unknown fields"):
            bridge_module.BridgeConfig.load(self.write_config(base))

    def test_rejects_traversal_and_incomplete_mtls(self):
        base = {
            "ump_socket": "/tmp/a", "controller_base_url": "https://example.com",
            "token_env": "TOKEN", "capabilities": {"x": {"path": "/../admin"}}
        }
        with self.assertRaisesRegex(bridge_module.BridgeError, "forbidden"):
            bridge_module.BridgeConfig.load(self.write_config(base))
        base["capabilities"]["x"]["path"] = "/execute"
        base["client_certificate"] = "/tmp/cert"
        with self.assertRaisesRegex(bridge_module.BridgeError, "configured together"):
            bridge_module.BridgeConfig.load(self.write_config(base))


class LocalApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_newline_json_unix_api(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "adapter.sock")
            observed = []

            async def handle(reader, writer):
                observed.append(json.loads(await reader.readline()))
                writer.write(b'{"result":{"ok":true}}\n')
                await writer.drain()
                writer.close()

            unix_server = await asyncio.start_unix_server(handle, socket_path)
            try:
                result = await bridge_module.LocalApiClient(socket_path).call("ping", {})
            finally:
                unix_server.close()
                await unix_server.wait_closed()
            self.assertEqual(result, {"ok": True})
            self.assertEqual(observed[0]["version"], 1)
            self.assertEqual(observed[0]["method"], "ping")


if __name__ == "__main__":
    unittest.main()
