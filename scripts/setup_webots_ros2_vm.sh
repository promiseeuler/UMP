#!/usr/bin/env bash
set -euo pipefail

WEBOTS_VERSION="${WEBOTS_VERSION:-R2025a}"
WEBOTS_DEB="${WEBOTS_DEB:-webots_2025a_amd64.deb}"
REPOSITORY_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "UMP Webots VM setup requires an x86-64 Ubuntu guest." >&2
  echo "Webots ${WEBOTS_VERSION} for Linux is not published for ARM64." >&2
  exit 2
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "UMP Webots VM setup supports Ubuntu 24.04; found ${PRETTY_NAME:-unknown}." >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y curl git locales software-properties-common
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
sudo add-apt-repository universe -y

ros_apt_source_version=$(curl -fsSL \
  https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
  | sed -n 's/.*"tag_name": "\([^"]*\)".*/\1/p')
if [[ -z "$ros_apt_source_version" ]]; then
  echo "Could not determine the current ros2-apt-source release." >&2
  exit 1
fi
curl -fL --retry 8 --retry-delay 5 --retry-all-errors \
  -o /tmp/ros2-apt-source.deb \
  "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ros_apt_source_version}/ros2-apt-source_${ros_apt_source_version}.noble_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb

sudo apt-get update
sudo apt-get install -y \
  python3-dev \
  python3-pip \
  python3-rosdep \
  ros-dev-tools \
  ros-jazzy-desktop \
  ros-jazzy-webots-ros2-driver \
  xauth \
  xvfb

if ! dpkg-query -W -f='${db:Status-Abbrev}' webots 2>/dev/null | grep -q '^ii '; then
  curl -fL --retry 12 --retry-delay 10 --retry-all-errors \
    --continue-at - -o "/tmp/${WEBOTS_DEB}" \
    "https://github.com/cyberbotics/webots/releases/download/${WEBOTS_VERSION}/${WEBOTS_DEB}"
  sudo apt-get install -y "/tmp/${WEBOTS_DEB}"
fi

if [[ ! -f "$REPOSITORY_ROOT/pyproject.toml" ]]; then
  echo "Repository root not found at $REPOSITORY_ROOT." >&2
  exit 2
fi

cd "$REPOSITORY_ROOT"
# Archives created on macOS can contain AppleDouble sidecars that pip mistakes
# for package metadata. They are not part of the project and are safe to drop.
find . -type f \( -name '._*' -o -name '.DS_Store' \) -delete
python3 -m pip install --break-system-packages --ignore-installed -e .
sudo rosdep init 2>/dev/null || true
rosdep update
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths ros2_ws/src --ignore-src -r -y
cd ros2_ws
colcon build --symlink-install --event-handlers console_direct+

cat <<EOF

UMP Webots VM setup complete.

Run the 3D demonstration:
  cd ${REPOSITORY_ROOT}
  source /opt/ros/jazzy/setup.bash
  source ros2_ws/install/setup.bash
  ros2 launch ump_webots_demo warehouse.launch.py

Run the retained headless smoke test:
  cd ${REPOSITORY_ROOT}
  source /opt/ros/jazzy/setup.bash
  source ros2_ws/install/setup.bash
  xvfb-run -a timeout 240s bash ros2_ws/webots_smoke.sh
EOF
