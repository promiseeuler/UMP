#!/usr/bin/env bash
set -euo pipefail

ros_package=${1:?usage: test-deb.sh ROS.deb}
test -f "$ros_package"
architecture=${UMP_PACKAGE_ARCH_OVERRIDE:-$(dpkg-deb --field "$ros_package" Architecture)}
case "$architecture" in
  amd64) platform=linux/amd64 ;;
  arm64) platform=linux/arm64 ;;
  *) echo "unsupported package architecture: $architecture" >&2; exit 2 ;;
esac
package_dir=$(cd "$(dirname "$ros_package")" && pwd)
ros_name=$(basename "$ros_package")

docker run --rm --platform "$platform" --entrypoint /bin/bash \
  -v "$package_dir:/packages:ro" \
  -e ROS_PACKAGE="/packages/$ros_name" \
  -e EXPECTED_ARCH="$architecture" \
  ump-ros2:phase4 -eo pipefail -c '
    source /opt/ros/jazzy/setup.bash
    if ros2 pkg prefix ump_ros2_adapter >/dev/null 2>&1; then
      echo "development workspace leaked into clean package test" >&2
      exit 1
    fi
    test "$(dpkg-deb --field "$ROS_PACKAGE" Architecture)" = "$EXPECTED_ARCH"
    dpkg-deb --field "$ROS_PACKAGE" Depends | grep -q "ump (>= "
    dpkg --ignore-depends=ump -i "$ROS_PACKAGE" >/dev/null
    test "$(dpkg-query -W -f="\${Status}" ump-ros2-jazzy)" = "install ok installed"
    source /opt/ump/ros/jazzy/setup.bash
    if find /opt/ump/ros/jazzy -type f \( -name "*.sh" -o -name "*.dsv" \) \
      -exec grep -l /workspaces/ump_ros2 {} + | grep -q .; then
      echo "development prefix leaked into installed environment hooks" >&2
      exit 1
    fi
    test "$(ros2 pkg prefix ump_interfaces)" = /opt/ump/ros/jazzy
    test "$(ros2 pkg prefix ump_ros2_adapter)" = /opt/ump/ros/jazzy
    test "$(ros2 pkg prefix ump_gazebo)" = /opt/ump/ros/jazzy
    ! ldd /opt/ump/ros/jazzy/lib/libump_payload_handoff.so | grep -q "not found"
    ! ldd /opt/ump/ros/jazzy/lib/libump_zone_monitor.so | grep -q "not found"
    ros2 interface show ump_interfaces/action/ExecuteCapability >/dev/null
    python3 -c "import ump_interfaces, ump_ros2_adapter"
    ros2 launch ump_gazebo s4_headless.launch.py --show-args >/dev/null
  '
echo "ROS 2 Jazzy package validation passed: $ros_package"
