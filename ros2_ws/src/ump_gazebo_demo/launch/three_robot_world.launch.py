from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    demo_share = Path(get_package_share_directory("ump_gazebo_demo"))
    ros_gz_share = Path(get_package_share_directory("ros_gz_sim"))
    world = demo_share / "worlds" / "three_robot_world.sdf"
    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            str(ros_gz_share / "launch" / "gz_sim.launch.py")
        ),
        launch_arguments={
            "gz_args": f"-r -v4 {world}",
            "on_exit_shutdown": "true",
        }.items(),
    )
    action_servers = [
        Node(
            package="ump_gazebo_demo",
            executable="proxy_action_server",
            namespace=namespace,
            name="capability_server",
            parameters=[
                {
                    "action_name": "execute_capability",
                    "capability": capability,
                    "duration_s": duration,
                }
            ],
            output="screen",
        )
        for namespace, capability, duration in (
            ("robot_quadruped_1", "ump.navigation.inspect-route/v1", 2.0),
            ("robot_humanoid_1", "ump.material.carry/v1", 3.0),
            ("robot_mobile_arm_1", "ump.manipulation.place/v1", 2.0),
        )
    ]
    return LaunchDescription([gazebo, *action_servers])
