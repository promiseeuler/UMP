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
        self.assertEqual(len(package_files), 3)
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
        sources += list((ROS / "ump_webots_demo").rglob("*.py"))
        self.assertGreaterEqual(len(sources), 7)
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
        self.assertIn("ros-jazzy-webots-ros2-driver", workflow)
        self.assertIn("webots_2025a_amd64.deb", workflow)
        self.assertIn("--ignore-installed .", workflow)
        self.assertIn("colcon test-result --verbose", workflow)
        self.assertIn(
            "pip install --break-system-packages --ignore-installed .", workflow
        )
        self.assertIn("timeout 180s bash ros2_ws/smoke.sh", workflow)
        self.assertIn("timeout 240s bash ros2_ws/webots_smoke.sh", workflow)
        self.assertIn(
            "ump-ros2-evidence /tmp/ump-ros2-webots-smoke.json", workflow
        )
        self.assertIn("ump-ros2-evidence /tmp/ump-ros2-gazebo-smoke.json", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("actions/attest@v4", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("attestations: write", workflow)
        self.assertIn("artifact-metadata: write", workflow)
        self.assertIn(
            "github.event.repository.visibility == 'public'",
            workflow,
        )
        self.assertNotIn("@main", workflow)

    def test_runtime_smoke_covers_world_and_action_lifecycle(self):
        smoke = (ROOT / "ros2_ws" / "smoke.sh").read_text()
        self.assertIn("gz sim -s -r", smoke)
        self.assertIn("/world/ump_conformance/control", smoke)
        self.assertEqual(smoke.count("--expect succeeded"), 2)
        self.assertEqual(smoke.count("--expect cancelled"), 1)
        self.assertEqual(smoke.count("--expect rejected"), 1)
        self.assertIn("ros2 run ump_gazebo_demo adapter_smoke_client", smoke)
        self.assertIn("--inputs-json", smoke)
        self.assertIn('"profile": "ump.ros2-gazebo-smoke/v1"', smoke)
        self.assertIn("UMP_SMOKE_REPORT", smoke)

        setup = (ROS / "ump_gazebo_demo" / "setup.py").read_text()
        self.assertIn("adapter_smoke_client:main", setup)

    def test_python_package_uses_exported_build_type_not_rosdep_key(self):
        package = ET.parse(ROS / "ump_gazebo_demo" / "package.xml").getroot()
        build_tools = {item.text for item in package.findall("buildtool_depend")}
        self.assertNotIn("ament_python", build_tools)
        export = package.find("export")
        assert export is not None
        self.assertEqual(export.findtext("build_type"), "ament_python")

    def test_webots_profile_contains_three_drivers_and_ump_capabilities(self):
        package = ROS / "ump_webots_demo"
        world = (package / "worlds" / "ump_warehouse.wbt").read_text()
        for robot_name in (
            "robot_quadruped_1",
            "robot_humanoid_1",
            "robot_mobile_arm_1",
        ):
            self.assertIn(f'name "{robot_name}"', world)
        self.assertEqual(world.count('controller "<extern>"'), 3)
        self.assertIn("DEF PACKAGE_1 Solid", world)
        self.assertIn('name "lidar"', world)

        expected = {
            "quadruped.urdf": "ump.navigation.inspect-route/v1",
            "humanoid.urdf": "ump.material.carry/v1",
            "mobile_arm.urdf": "ump.manipulation.place/v1",
        }
        for filename, capability in expected.items():
            root = ET.parse(package / "resource" / filename).getroot()
            plugin = root.find("./webots/plugin")
            assert plugin is not None
            self.assertEqual(
                plugin.attrib["type"],
                "ump_webots_demo.warehouse_driver.WarehouseRobotDriver",
            )
            self.assertEqual(plugin.findtext("capability"), capability)

        launch = (package / "launch" / "warehouse.launch.py").read_text()
        self.assertIn("WebotsLauncher", launch)
        self.assertIn("WebotsController", launch)
        self.assertIn('DeclareLaunchArgument("autostart"', launch)

    def test_webots_smoke_runs_the_ordered_warehouse_flow(self):
        smoke = (ROOT / "ros2_ws" / "webots_smoke.sh").read_text()
        self.assertIn("warehouse.launch.py", smoke)
        self.assertIn("warehouse_smoke_client", smoke)
        client = (
            ROS
            / "ump_webots_demo"
            / "ump_webots_demo"
            / "warehouse_smoke_client.py"
        ).read_text()
        positions = [
            client.index("ump.navigation.inspect-route/v1"),
            client.index("ump.material.carry/v1"),
            client.index("ump.manipulation.place/v1"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('"profile": "ump.ros2-webots-warehouse/v1"', client)
        self.assertIn("WarehousePlanner", client)
        self.assertIn("Ros2RobotAdapter", client)
        self.assertIn('"ump_planner_exercised": True', client)


if __name__ == "__main__":
    unittest.main()
