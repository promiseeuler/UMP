from glob import glob
from setuptools import find_packages, setup


package_name = "ump_gazebo_demo"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/worlds", glob("worlds/*.sdf")),
    ],
    install_requires=["setuptools"],
    tests_require=["pytest"],
    zip_safe=True,
    maintainer="Promise Euler",
    maintainer_email="promiseeuler@users.noreply.github.com",
    description="Gazebo Harmonic conformance world for UMP",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "proxy_action_server = ump_gazebo_demo.proxy_action_server:main",
            "action_smoke_client = ump_gazebo_demo.action_smoke_client:main",
        ],
    },
)
