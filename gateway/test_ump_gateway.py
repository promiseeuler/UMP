import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("ump_gateway.py")
SPEC = importlib.util.spec_from_file_location("ump_gateway", MODULE_PATH)
gateway_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = gateway_module
SPEC.loader.exec_module(gateway_module)


class FakeApi:
    def __init__(self, machine_id="ump:machine:arm-1", task=None):
        self.machine_id = machine_id
        self.task = task
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "ping":
            return {
                "machine_id": self.machine_id,
                "state_revision": 4,
                "proxy": {"gateway_id": "ump:gateway:test-1"},
            }
        if method == "next_task":
            value, self.task = self.task, None
            return {"task": value}
        return {}


class FakeController:
    def __init__(self, observation=None, failure=None):
        self.observation = observation or {
            "operational": "idle", "safety": "normal", "health_codes": []
        }
        self.failure = failure
        self.probes = 0
        self.observations = 0

    def probe(self):
        self.probes += 1
        if self.failure:
            raise self.failure

    def observe(self):
        self.observations += 1
        if self.failure:
            raise self.failure
        return self.observation


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.gateway_config = gateway_module.GatewayConfig(
            "ump:gateway:test-1", "ump:machine:arm-1", "test_https",
            root / "config.json", root / "bridge.json", root / "status.json",
        )

    def bridge_config(self, read_only=False):
        return gateway_module.http_bridge.BridgeConfig(
            ump_socket="/tmp/adapter.sock",
            controller_base_url="http://127.0.0.1:1",
            token_env=None,
            ca_file=None,
            client_certificate=None,
            client_key=None,
            allow_insecure_loopback=True,
            read_only=read_only,
            poll_interval_seconds=0.01,
            capabilities={},
            health_path=None if read_only else "/health",
            observation_path="/observe" if read_only else None,
        )

    async def test_command_gateway_probes_before_claiming(self):
        api = FakeApi()
        controller = FakeController()
        supervisor = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(), api, controller
        )
        await supervisor.cycle()
        self.assertEqual(controller.probes, 1)
        methods = [method for method, _ in api.calls]
        self.assertLess(methods.index("state_update"), methods.index("next_task"))
        status = json.loads(self.gateway_config.status_file.read_text())
        self.assertEqual(status["connectivity"], "connected")
        self.assertTrue(status["proxy"])

    async def test_read_only_observes_and_never_claims(self):
        api = FakeApi()
        controller = FakeController()
        supervisor = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(read_only=True), api, controller
        )
        await supervisor.cycle()
        self.assertEqual(controller.observations, 1)
        self.assertNotIn("next_task", [method for method, _ in api.calls])
        state = [params for method, params in api.calls if method == "state_update"][0]
        self.assertEqual(state["safety"], "normal")

    async def test_controller_disconnect_is_explicit_and_does_not_claim(self):
        api = FakeApi()
        failure = gateway_module.http_bridge.BridgeError(
            "bridge.controller_unavailable", "offline", True
        )
        supervisor = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(), api, FakeController(failure=failure)
        )
        await supervisor.cycle()
        self.assertNotIn("next_task", [method for method, _ in api.calls])
        status = json.loads(self.gateway_config.status_file.read_text())
        self.assertEqual(status["connectivity"], "controller_disconnected")
        self.assertEqual(status["fault"], "bridge.controller_unavailable")

    async def test_identity_mismatch_disables_gateway(self):
        supervisor = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(), FakeApi("ump:machine:wrong"), FakeController()
        )
        with self.assertRaisesRegex(gateway_module.http_bridge.BridgeError, "identity"):
            await supervisor.cycle()
        status = json.loads(self.gateway_config.status_file.read_text())
        self.assertEqual(status["connectivity"], "identity_mismatch")

    async def test_status_revision_advances_after_restart(self):
        first = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(), FakeApi(), FakeController()
        )
        await first.cycle()
        previous = json.loads(self.gateway_config.status_file.read_text())["revision"]
        second = gateway_module.GatewaySupervisor(
            self.gateway_config, self.bridge_config(), FakeApi(), FakeController()
        )
        await second.cycle()
        current = json.loads(self.gateway_config.status_file.read_text())["revision"]
        self.assertGreater(current, previous)

    def test_configure_runtime_binds_proxy_identity(self):
        self.gateway_config.runtime_config.write_text(json.dumps({
            "machine_id": "ump:machine:arm-1", "machine_class": "robot_arm", "protocol_minor": 2
        }))
        self.gateway_config.configure_runtime(self.bridge_config(read_only=True))
        runtime = json.loads(self.gateway_config.runtime_config.read_text())
        self.assertEqual(runtime["gateway_proxy"]["gateway_id"], "ump:gateway:test-1")
        self.assertTrue(runtime["gateway_proxy"]["read_only"])
        self.assertEqual(os.stat(self.gateway_config.runtime_config).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
