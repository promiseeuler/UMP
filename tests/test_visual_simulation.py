from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import urlopen
import unittest

from ump.visual_simulation import VisualSimulationServer, build_visual_scenario


class VisualSimulationTests(unittest.TestCase):
    def test_scenario_runs_real_three_robot_collaboration(self):
        scenario = build_visual_scenario()
        self.assertEqual(scenario["profile"], "ump.visual-simulation/v1")
        self.assertFalse(scenario["simulation"]["physical_dynamics"])
        self.assertEqual(scenario["plan"]["status"], "succeeded")
        self.assertEqual(len(scenario["robots"]), 3)
        self.assertEqual(len(scenario["plan"]["steps"]), 3)
        self.assertEqual(scenario["message_counts"]["assignment"], 3)
        self.assertEqual(scenario["message_counts"]["outcome"], 3)

    def test_server_exposes_self_contained_ui_and_restrictive_headers(self):
        server = VisualSimulationServer(port=0)
        address = server.start()
        root = f"http://{address.host}:{address.port}"
        try:
            with urlopen(f"{root}/api/scenario", timeout=3) as response:
                scenario = json.loads(response.read())
                self.assertEqual(scenario["plan"]["status"], "succeeded")
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            for path, marker in (
                ("/", "UMP Visual Simulation"),
                ("/app.js", "loadScenario"),
                ("/styles.css", "simulation-workspace"),
            ):
                with urlopen(f"{root}{path}", timeout=3) as response:
                    self.assertIn(marker, response.read().decode())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            with self.assertRaises(HTTPError) as missing:
                urlopen(f"{root}/missing", timeout=3)
            try:
                self.assertEqual(missing.exception.code, 404)
            finally:
                missing.exception.close()
        finally:
            server.stop()

    def test_remote_binding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            VisualSimulationServer("0.0.0.0", 0)


if __name__ == "__main__":
    unittest.main()
