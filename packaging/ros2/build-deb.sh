#!/usr/bin/env bash
set -euo pipefail

prefix=${UMP_ROS_PREFIX:-/opt/ump/ros/jazzy}
version=${UMP_PACKAGE_VERSION:-0.1.0}
output_dir=${UMP_PACKAGE_OUTPUT_DIR:-/dist}
architecture=${UMP_PACKAGE_ARCH:-$(dpkg --print-architecture)}
source_revision=${UMP_SOURCE_REVISION:-unknown}
case "$architecture" in
  amd64|arm64) ;;
  *) echo "UMP_PACKAGE_ARCH must be amd64 or arm64" >&2; exit 2 ;;
esac
dpkg --validate-version "$version" >/dev/null 2>&1 || {
  echo "invalid Debian package version: $version" >&2
  exit 2
}
test -f "$prefix/setup.bash" || { echo "missing ROS install prefix: $prefix" >&2; exit 2; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
package_root="$stage/ump-ros2-jazzy"
install -d "$package_root/DEBIAN" "$package_root/opt/ump/ros/jazzy" \
  "$package_root/etc/profile.d" "$package_root/usr/share/doc/ump-ros2-jazzy"
cp -a "$prefix/." "$package_root/opt/ump/ros/jazzy/"
cat >"$package_root/etc/profile.d/ump-ros2-jazzy.sh" <<'EOF'
if test -f /opt/ros/jazzy/setup.sh; then
  . /opt/ros/jazzy/setup.sh
fi
if test -f /opt/ump/ros/jazzy/setup.sh; then
  . /opt/ump/ros/jazzy/setup.sh
fi
EOF
chmod 0644 "$package_root/etc/profile.d/ump-ros2-jazzy.sh"
cat >"$package_root/usr/share/doc/ump-ros2-jazzy/build.json" <<EOF
{"source_revision":"$source_revision","ros_distribution":"jazzy","ubuntu":"24.04","architecture":"$architecture"}
EOF
installed_size=$(du -sk "$package_root/opt" "$package_root/etc" | awk '{sum += $1} END {print sum}')
cat >"$package_root/DEBIAN/control" <<EOF
Package: ump-ros2-jazzy
Version: $version
Section: devel
Priority: optional
Architecture: $architecture
Maintainer: UMP Contributors <maintainers@ump.dev>
Installed-Size: $installed_size
Depends: ump (>= $version), python3, ros-jazzy-builtin-interfaces, ros-jazzy-diagnostic-msgs, ros-jazzy-geometry-msgs, ros-jazzy-nav-msgs, ros-jazzy-rclpy, ros-jazzy-ros-gz-bridge, ros-jazzy-ros-gz-sim, ros-jazzy-rosidl-default-runtime, ros-jazzy-std-msgs, ros-jazzy-tf2-ros
Description: Universal Machine Protocol adapter for ROS 2 Jazzy
 Lifecycle, diagnostics, TF, action, controller, and Gazebo integration for UMP.
EOF

mkdir -p "$output_dir"
output="$output_dir/ump-ros2-jazzy_${version}_${architecture}.deb"
dpkg-deb --root-owner-group --build "$package_root" "$output" >/dev/null
echo "$output"
