#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/jazzy/setup.bash
source /workspaces/ump_ros2/install/setup.bash
exec "$@"
