from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    gazebo_share = Path(get_package_share_directory("ump_gazebo"))
    adapter_share = Path(get_package_share_directory("ump_ros2_adapter"))
    mobile_adapter = adapter_share / "config" / "mobile_adapter.yaml"
    arm_adapter = adapter_share / "config" / "arm_adapter.yaml"

    return LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(gazebo_share / "launch" / "s4_headless.launch.py")
                )
            ),
            Node(
                package="ump_ros2_adapter",
                executable="s4_controller",
                name="mobile_controller",
                parameters=[
                    {
                        "role": "mobile",
                        "action_name": "/mobile_controller/execute_capability",
                        "use_sim_time": True,
                    }
                ],
                output="screen",
            ),
            Node(
                package="ump_ros2_adapter",
                executable="s4_controller",
                name="arm_controller",
                parameters=[
                    {
                        "role": "arm",
                        "action_name": "/arm_controller/execute_capability",
                        "use_sim_time": True,
                    }
                ],
                output="screen",
            ),
            Node(
                package="ump_ros2_adapter",
                executable="adapter",
                namespace="mobile",
                name="ump_ros2_adapter",
                parameters=[str(mobile_adapter), {"use_sim_time": True}],
                output="screen",
            ),
            Node(
                package="ump_ros2_adapter",
                executable="adapter",
                namespace="arm",
                name="ump_ros2_adapter",
                parameters=[str(arm_adapter), {"use_sim_time": True}],
                output="screen",
            ),
            Node(
                package="tf2_ros",
                executable="static_transform_publisher",
                name="arm_tool_transform",
                arguments=[
                    "--x", "0", "--y", "1.65", "--z", "1.38",
                    "--yaw", "-1.5708", "--frame-id", "world",
                    "--child-frame-id", "robot_arm/tool0",
                ],
                output="screen",
            ),
            Node(
                package="ump_ros2_adapter",
                executable="zone_monitor",
                name="ump_zone_monitor",
                parameters=[
                    {"local_socket": "/run/ump/zone/adapter.sock"},
                    {"resource_id": "ump:resource:s4-transfer-zone"},
                ],
                output="screen",
            ),
            Node(
                package="ump_ros2_adapter",
                executable="payload_monitor",
                name="ump_payload_monitor",
                parameters=[
                    {"local_socket": "/run/ump/zone/adapter.sock"},
                    {"subject_id": "ump:subject:s4-package"},
                ],
                output="screen",
            ),
            TimerAction(
                period=2.0,
                actions=[
                    ExecuteProcess(
                        cmd=["ros2", "lifecycle", "set", "/mobile/ump_ros2_adapter", "configure"]
                    ),
                    ExecuteProcess(
                        cmd=["ros2", "lifecycle", "set", "/arm/ump_ros2_adapter", "configure"]
                    ),
                ],
            ),
            TimerAction(
                period=3.0,
                actions=[
                    ExecuteProcess(
                        cmd=["ros2", "lifecycle", "set", "/mobile/ump_ros2_adapter", "activate"]
                    ),
                    ExecuteProcess(
                        cmd=["ros2", "lifecycle", "set", "/arm/ump_ros2_adapter", "activate"]
                    ),
                ],
            ),
        ]
    )
