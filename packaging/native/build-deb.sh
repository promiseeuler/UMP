#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
version=${UMP_PACKAGE_VERSION:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$root/Cargo.toml" | head -1)}
binary_dir=${UMP_BINARY_DIR:-$root/target/release}
output_dir=${UMP_PACKAGE_OUTPUT_DIR:-$root/dist}
architecture=${UMP_PACKAGE_ARCH:-}

if test -z "$version"; then
  echo "unable to determine package version" >&2
  exit 2
fi
if test -z "$architecture"; then
  case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    aarch64|arm64) architecture=arm64 ;;
    *) echo "unsupported package architecture: $(uname -m)" >&2; exit 2 ;;
  esac
fi
case "$architecture" in
  amd64|arm64) ;;
  *) echo "UMP_PACKAGE_ARCH must be amd64 or arm64" >&2; exit 2 ;;
esac
for binary in ump umpd; do
  test -x "$binary_dir/$binary" || {
    echo "missing executable $binary_dir/$binary; run cargo build --release -p ump-cli --bins" >&2
    exit 2
  }
done
command -v dpkg-deb >/dev/null || {
  echo "dpkg-deb is required to build the Debian package" >&2
  exit 2
}
dpkg --validate-version "$version" >/dev/null 2>&1 || {
  echo "invalid Debian package version: $version" >&2
  exit 2
}

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
package_root="$stage/ump"
install -d "$package_root/DEBIAN" "$package_root/usr/bin" \
  "$package_root/lib/systemd/system" "$package_root/usr/share/doc/ump"
install -m 0755 "$binary_dir/ump" "$binary_dir/umpd" "$package_root/usr/bin/"
install -m 0644 "$root/packaging/systemd/umpd.service" \
  "$package_root/lib/systemd/system/umpd.service"
install -m 0644 "$root/LICENSE" "$package_root/usr/share/doc/ump/copyright"
for script in postinst prerm postrm; do
  install -m 0755 "$root/packaging/debian/$script" "$package_root/DEBIAN/$script"
done
installed_size=$(du -sk "$package_root/usr" | awk '{print $1}')
cat >"$package_root/DEBIAN/control" <<EOF
Package: ump
Version: $version
Section: net
Priority: optional
Architecture: $architecture
Maintainer: UMP Contributors <maintainers@ump.dev>
Installed-Size: $installed_size
Depends: adduser, libc6
Description: Universal Machine Protocol runtime and command-line tools
 UMP provides authenticated machine discovery, task coordination, resource
 reservations, handoffs, and local adapter integration without a cloud service.
EOF

mkdir -p "$output_dir"
output="$output_dir/ump_${version}_${architecture}.deb"
dpkg-deb --root-owner-group --build "$package_root" "$output" >/dev/null
echo "$output"
