#!/usr/bin/env bash
set -euo pipefail

package=${1:?usage: test-deb.sh PACKAGE.deb}
test -f "$package"
metadata=$(dpkg-deb --field "$package")
grep -q '^Package: ump$' <<<"$metadata"
grep -Eq '^Architecture: (amd64|arm64)$' <<<"$metadata"
grep -q '^Depends: adduser, libc6$' <<<"$metadata"

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
dpkg-deb --extract "$package" "$root"
dpkg-deb --control "$package" "$root/DEBIAN"
test -x "$root/usr/bin/ump"
test -x "$root/usr/bin/umpd"
test -f "$root/lib/systemd/system/umpd.service"
test "$(stat -c %a "$root/DEBIAN/postinst")" = 755
test "$(stat -c %a "$root/DEBIAN/prerm")" = 755
test "$(stat -c %a "$root/DEBIAN/postrm")" = 755
grep -q '^User=ump$' "$root/lib/systemd/system/umpd.service"
grep -q '^StateDirectoryMode=0700$' "$root/lib/systemd/system/umpd.service"
"$root/usr/bin/ump" --help >/dev/null
echo "Debian package validation passed: $package"
