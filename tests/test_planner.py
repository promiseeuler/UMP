from pathlib import Path
import unittest

from ump.demo import WarehousePlanner
from ump.planner import Planner, load_planner


class PlannerLoaderTests(unittest.TestCase):
    def test_loads_reference_planner_factory(self):
        planner = load_planner("ump.demo:create_warehouse_planner")

        self.assertIsInstance(planner, Planner)
        self.assertEqual(planner.planner_id, WarehousePlanner.planner_id)

    def test_rejects_invalid_specification(self):
        with self.assertRaisesRegex(ValueError, "module:factory"):
            load_planner("ump.demo")

    def test_rejects_missing_factory(self):
        with self.assertRaisesRegex(ValueError, "cannot be loaded"):
            load_planner("ump.demo:missing")

    def test_rejects_non_callable_factory(self):
        with self.assertRaisesRegex(ValueError, "must be callable"):
            load_planner("ump.demo:__name__")

    def test_passes_configuration_path_to_factory(self):
        planner = load_planner(
            "tests.test_planner:create_configured_planner", "planner.json"
        )

        self.assertEqual(planner.config_path, Path("planner.json"))

    def test_rejects_factory_result_without_planner_contract(self):
        with self.assertRaisesRegex(TypeError, "did not return a Planner"):
            load_planner("tests.test_planner:create_non_planner")

    def test_rejects_empty_planner_identity(self):
        with self.assertRaisesRegex(ValueError, "planner_id must be non-empty"):
            load_planner("tests.test_planner:create_unnamed_planner")


class ConfiguredPlanner:
    planner_id = "ump.test.configured/v1"

    def __init__(self, config_path: Path | None) -> None:
        self.config_path = config_path

    def propose(self, goal, manifests, states):
        raise NotImplementedError


def create_configured_planner(config_path: Path | None) -> ConfiguredPlanner:
    return ConfiguredPlanner(config_path)


def create_non_planner(config_path: Path | None) -> object:
    del config_path
    return object()


def create_unnamed_planner(config_path: Path | None) -> ConfiguredPlanner:
    planner = ConfiguredPlanner(config_path)
    planner.planner_id = ""
    return planner


if __name__ == "__main__":
    unittest.main()
