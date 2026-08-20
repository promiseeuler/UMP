import ast
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


ROOT = Path(__file__).parents[1]
ROS = ROOT / "ros2_ws" / "src"


class Ros2PackagingTests(unittest.TestCase):
    def test_ros_packages_have_valid_metadata_and_unique_names(self):
        package_files = sorted(ROS.glob("*/package.xml"))
        self.assertEqual(len(package_files), 2)
        names = []
        for package_file in package_files:
            root = ET.parse(package_file).getroot()
            names.append(root.findtext("name"))
            self.assertEqual(root.findtext("license"), "Apache-2.0")
            self.assertTrue(root.findtext("maintainer"))
        self.assertEqual(len(names), len(set(names)))

    def test_execute_capability_action_has_goal_result_and_feedback_contract(self):
        action = (
            ROS / "ump_interfaces" / "action" / "ExecuteCapability.action"
        ).read_text()
        sections = action.split("---")
        self.assertEqual(len(sections), 3)
        self.assertIn("string assignment_id", sections[0])
        self.assertIn("string inputs_json", sections[0])
        self.assertIn("uint8 UNKNOWN=4", sections[1])
        self.assertIn("float32 progress", sections[2])

    def test_gazebo_world_contains_three_named_proxies_and_required_systems(self):
        world_path = (
            ROS / "ump_gazebo_demo" / "worlds" / "three_robot_world.sdf"
        )
        root = ET.parse(world_path).getroot()
        world = root.find("world")
        assert world is not None
        model_names = {model.attrib["name"] for model in world.findall("model")}
        self.assertTrue(
            {
                "robot_humanoid_proxy",
                "robot_quadruped_proxy",
                "robot_mobile_arm_proxy",
            }
            <= model_names
        )
        plugins = {plugin.attrib["filename"] for plugin in world.findall("plugin")}
        self.assertTrue(
            {
                "gz-sim-physics-system",
                "gz-sim-user-commands-system",
                "gz-sim-scene-broadcaster-system",
            }
            <= plugins
        )

    def test_ros_python_sources_parse_without_importing_optional_runtime(self):
        sources = list((ROS / "ump_gazebo_demo").rglob("*.py"))
        self.assertGreaterEqual(len(sources), 3)
        for source in sources:
            with self.subTest(source=source):
                ast.parse(source.read_text(), filename=str(source))

    def test_ros_ci_is_pinned_to_the_supported_jazzy_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "ros2.yml").read_text()
        self.assertIn("image: ubuntu:noble", workflow)
        self.assertIn("required-ros-distributions: jazzy", workflow)
        self.assertIn("source /opt/ros/jazzy/setup.bash", workflow)
        self.assertIn("ros-tooling/setup-ros@v0.7", workflow)
        self.assertIn("ros-jazzy-ros-gz-sim", workflow)
        self.assertIn("colcon test-result --verbose", workflow)
        self.assertIn("timeout 180s bash ros2_ws/smoke.sh", workflow)
        self.assertNotIn("@main", workflow)

    def test_runtime_smoke_covers_world_and_action_lifecycle(self):
        smoke = (ROOT / "ros2_ws" / "smoke.sh").read_text()
        self.assertIn("gz sim -s -r", smoke)
        self.assertIn("/world/ump_conformance/control", smoke)
        self.assertEqual(smoke.count("--expect succeeded"), 2)
        self.assertEqual(smoke.count("--expect cancelled"), 1)
        self.assertEqual(smoke.count("--expect rejected"), 1)

    def test_python_package_uses_exported_build_type_not_rosdep_key(self):
        package = ET.parse(ROS / "ump_gazebo_demo" / "package.xml").getroot()
        build_tools = {item.text for item in package.findall("buildtool_depend")}
        self.assertNotIn("ament_python", build_tools)
        export = package.find("export")
        assert export is not None
        self.assertEqual(export.findtext("build_type"), "ament_python")


if __name__ == "__main__":
    unittest.main()
