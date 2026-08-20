#!/usr/bin/env bash
set -euo pipefail

ros_package=${1:?usage: test-s5-deb.sh ROS.deb}
test -f "$ros_package"
architecture=${UMP_PACKAGE_ARCH_OVERRIDE:-$(dpkg-deb --field "$ros_package" Architecture)}
case "$architecture" in
  amd64) platform=linux/amd64 ;;
  arm64) platform=linux/arm64 ;;
  *) echo "unsupported package architecture: $architecture" >&2; exit 2 ;;
esac
relative_package=$(python3 - "$ros_package" <<'PY'
from pathlib import Path
import sys
root = Path.cwd().resolve()
print(Path(sys.argv[1]).resolve().relative_to(root))
PY
)
image="ump-ros2-s5:${architecture}"
docker build --platform "$platform" -f packaging/ros2/S5.Dockerfile \
  --build-arg "ROS_DEB=$relative_package" -t "$image" .

artifacts=${UMP_S5_ROS_ARTIFACTS:-$(pwd)/artifacts/s5-ros-$architecture}
mkdir -p "$artifacts"
docker run --rm --platform "$platform" \
  -e ROS_DOMAIN_ID=55 -e UMP_S5_USE_ROS_ZONE=true \
  -v "$artifacts:/artifacts" \
  "$image" bash -eo pipefail -c '
    source /opt/ros/jazzy/setup.bash
    source /opt/ump/ros/jazzy/setup.bash
    set -u
    test "$(ros2 pkg prefix ump_ros2_adapter)" = /opt/ump/ros/jazzy
    dpkg-query -W ump >/dev/null
    dpkg-query -W ump-ros2-jazzy >/dev/null
    export UMP_S5_ARTIFACTS=/tmp/ump-s5-artifacts
    /opt/ump/sim/s5/run.sh
    cp -a /tmp/ump-s5-artifacts/. /artifacts/
  '
test -s "$artifacts/report.json"
grep -q '"passed": true' "$artifacts/report.json"
grep -q '"installed_ros_package_prefix": true' "$artifacts/report.json"
echo "Installed ROS 2 Jazzy S5 passed: $architecture; artifacts: $artifacts"
