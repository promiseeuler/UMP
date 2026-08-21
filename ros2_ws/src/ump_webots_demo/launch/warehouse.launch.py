from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler, TimerAction
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from webots_ros2_driver.webots_controller import WebotsController
from webots_ros2_driver.webots_launcher import WebotsLauncher


def generate_launch_description():
    share = Path(get_package_share_directory("ump_webots_demo"))
    world = share / "worlds" / "ump_warehouse.wbt"
    mode = LaunchConfiguration("mode")
    autostart = LaunchConfiguration("autostart")
    webots = WebotsLauncher(world=str(world), mode=mode, ros2_supervisor=True)

    drivers = [
        WebotsController(
            robot_name=robot_name,
            namespace=robot_name,
            parameters=[{
                "robot_description": str(share / "resource" / urdf),
                "use_sim_time": True,
            }],
            respawn=True,
        )
        for robot_name, urdf in (
            ("robot_quadruped_1", "quadruped.urdf"),
            ("robot_humanoid_1", "humanoid.urdf"),
            ("robot_mobile_arm_1", "mobile_arm.urdf"),
        )
    ]
    coordinator = TimerAction(
        period=5.0,
        condition=IfCondition(autostart),
        actions=[Node(
            package="ump_webots_demo",
            executable="warehouse_smoke_client",
            name="warehouse_coordinator",
            output="screen",
            arguments=["--world", str(world)],
        )],
    )
    shutdown = RegisterEventHandler(
        OnProcessExit(target_action=webots, on_exit=[EmitEvent(event=Shutdown())])
    )
    return LaunchDescription([
        DeclareLaunchArgument("mode", default_value="realtime"),
        DeclareLaunchArgument("autostart", default_value="true"),
        webots,
        webots._supervisor,
        *drivers,
        coordinator,
        shutdown,
    ])
