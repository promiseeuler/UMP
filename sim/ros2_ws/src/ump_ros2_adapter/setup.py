from glob import glob
from setuptools import find_packages, setup

package_name = "ump_ros2_adapter"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    tests_require=["pytest"],
    zip_safe=True,
    maintainer="UMP maintainers",
    maintainer_email="maintainers@ump.dev",
    description="Lifecycle-managed ROS 2 adapter for UMP machines.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "adapter = ump_ros2_adapter.node:main",
            "payload_monitor = ump_ros2_adapter.payload_monitor:main",
            "s4_controller = ump_ros2_adapter.s4_controller:main",
            "zone_monitor = ump_ros2_adapter.zone_monitor:main",
        ],
    },
)
