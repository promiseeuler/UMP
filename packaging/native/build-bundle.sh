#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
version=${UMP_PACKAGE_VERSION:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$root/Cargo.toml" | head -1)}
binary_dir=${UMP_BINARY_DIR:-$root/target/release}
output_dir=${UMP_PACKAGE_OUTPUT_DIR:-$root/dist}
architecture=${UMP_PACKAGE_ARCH:-}
source_revision=${UMP_SOURCE_REVISION:-$(git -C "$root" rev-parse HEAD 2>/dev/null || echo unknown)}
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$root" show -s --format=%ct HEAD 2>/dev/null || date +%s)}

if test -z "$architecture"; then
  case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    aarch64|arm64) architecture=arm64 ;;
    *) echo "unsupported bundle architecture: $(uname -m)" >&2; exit 2 ;;
  esac
fi
case "$architecture" in
  amd64|arm64) ;;
  *) echo "UMP_PACKAGE_ARCH must be amd64 or arm64" >&2; exit 2 ;;
esac
test -n "$version" || { echo "unable to determine bundle version" >&2; exit 2; }
for command in sha256sum tar gzip; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 2; }
done
for binary in ump umpd; do
  test -x "$binary_dir/$binary" || { echo "missing executable $binary_dir/$binary" >&2; exit 2; }
done

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
bundle_name="ump_${version}_linux_${architecture}"
bundle_root="$stage/$bundle_name"
install -d "$bundle_root/bin" "$bundle_root/systemd"
install -m 0755 "$binary_dir/ump" "$binary_dir/umpd" "$bundle_root/bin/"
install -m 0644 "$root/packaging/systemd/umpd.service" "$bundle_root/systemd/"
install -m 0644 "$root/LICENSE" "$bundle_root/LICENSE"
ump_sha=$(sha256sum "$binary_dir/ump" | awk '{print $1}')
umpd_sha=$(sha256sum "$binary_dir/umpd" | awk '{print $1}')
cat >"$bundle_root/manifest.json" <<EOF
{
  "artifact": "ump-standalone-linux",
  "version": "$version",
  "architecture": "$architecture",
  "source_revision": "$source_revision",
  "binaries": {
    "ump": {"sha256": "$ump_sha"},
    "umpd": {"sha256": "$umpd_sha"}
  }
}
EOF
(cd "$bundle_root" && sha256sum bin/ump bin/umpd systemd/umpd.service LICENSE manifest.json >SHA256SUMS)
mkdir -p "$output_dir"
output="$output_dir/$bundle_name.tar.gz"
tar --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
  -C "$stage" -cf - "$bundle_name" | gzip -n >"$output"
echo "$output"
