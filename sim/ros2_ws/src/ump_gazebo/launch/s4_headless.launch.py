import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, SetEnvironmentVariable
from launch_ros.actions import Node


def generate_launch_description():
    share = Path(get_package_share_directory("ump_gazebo"))
    prefix = share.parents[1]
    world = share / "worlds" / "s4_warehouse.sdf"
    variant = os.environ.get("UMP_MOBILE_MODEL_VARIANT", "reference")
    if variant not in {"reference", "alternate"}:
        raise ValueError(f"unsupported UMP_MOBILE_MODEL_VARIANT: {variant}")
    model_paths = [share / "models"]
    if variant == "alternate":
        model_paths.insert(0, share / "models" / "alternate")
    return LaunchDescription(
        [
            SetEnvironmentVariable(
                "GZ_SIM_RESOURCE_PATH", os.pathsep.join(map(str, model_paths))
            ),
            SetEnvironmentVariable("GZ_SIM_SYSTEM_PLUGIN_PATH", str(prefix / "lib")),
            ExecuteProcess(
                cmd=["gz", "sim", "-r", "-s", "--iterations", "0", str(world)],
                output="screen",
            ),
            Node(
                package="ros_gz_bridge",
                executable="parameter_bridge",
                arguments=[
                    "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
                    "/model/mobile_base/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist",
                    "/model/mobile_base/odometry@nav_msgs/msg/Odometry[gz.msgs.Odometry",
                    "/model/robot_arm/shoulder@std_msgs/msg/Float64]gz.msgs.Double",
                    "/model/robot_arm/elbow@std_msgs/msg/Float64]gz.msgs.Double",
                    "/ump/payload/attach_arm@std_msgs/msg/Bool]gz.msgs.Boolean",
                    "/ump/payload/attach_mobile@std_msgs/msg/Bool]gz.msgs.Boolean",
                    "/ump/payload/detach@std_msgs/msg/Bool]gz.msgs.Boolean",
                    "/ump/payload/fail_next_attach@std_msgs/msg/Bool]gz.msgs.Boolean",
                    "/ump/payload/drop@std_msgs/msg/Bool]gz.msgs.Boolean",
                    "/ump/payload/state@std_msgs/msg/String[gz.msgs.StringMsg",
                    "/ump/zone/state@std_msgs/msg/String[gz.msgs.StringMsg",
                    "/world/s4/model/info@ros_gz_interfaces/msg/EntityFactory[gz.msgs.EntityFactory",
                ],
                output="screen",
            ),
        ]
    )
