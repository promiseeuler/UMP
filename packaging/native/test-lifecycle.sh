#!/usr/bin/env bash
set -euo pipefail

old_package=${1:?usage: test-lifecycle.sh OLD.deb NEW.deb}
new_package=${2:?usage: test-lifecycle.sh OLD.deb NEW.deb}
for package in "$old_package" "$new_package"; do
  test -f "$package"
done
old_arch=${UMP_PACKAGE_ARCH_OVERRIDE:-$(dpkg-deb --field "$old_package" Architecture)}
new_arch=${UMP_PACKAGE_ARCH_OVERRIDE:-$(dpkg-deb --field "$new_package" Architecture)}
old_version=${UMP_OLD_VERSION_OVERRIDE:-$(dpkg-deb --field "$old_package" Version)}
new_version=${UMP_NEW_VERSION_OVERRIDE:-$(dpkg-deb --field "$new_package" Version)}
test "$old_arch" = "$new_arch"
case "$old_arch" in
  amd64) platform=linux/amd64 ;;
  arm64) platform=linux/arm64 ;;
  *) echo "unsupported package architecture: $old_arch" >&2; exit 2 ;;
esac

package_dir=$(cd "$(dirname "$old_package")" && pwd)
old_name=$(basename "$old_package")
new_name=$(basename "$new_package")
docker run --rm --platform "$platform" -v "$package_dir:/packages:ro" \
  -e OLD_PACKAGE="/packages/$old_name" -e NEW_PACKAGE="/packages/$new_name" \
  -e OLD_VERSION="$old_version" -e NEW_VERSION="$new_version" \
  ubuntu:24.04 bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends adduser
    dpkg --compare-versions "$NEW_VERSION" gt "$OLD_VERSION"
    dpkg -i "$OLD_PACKAGE" >/dev/null
    test "$(dpkg-query -W ump | awk "{print \$2}")" = "$OLD_VERSION"
    runuser -u ump -- ump --data-dir /var/lib/ump init \
      --machine-id ump:machine:package-lifecycle \
      --machine-class test_fixture --listen 127.0.0.1:0 >/dev/null
    runuser -u ump -- ump --data-dir /var/lib/ump register-capability \
      --capability org.ump.test.lifecycle --interruptible >/dev/null
    runuser -u ump -- ump --data-dir /var/lib/ump run --once >/dev/null
    find /var/lib/ump -maxdepth 1 -type f \
      \( -name config.json -o -name development-root.der \
         -o -name identity.der -o -name identity-key.der \) \
      -print0 | sort -z | xargs -0 sha256sum >/tmp/identity.before
    sha256sum /var/lib/ump/config.json >/tmp/config.before
    dpkg -i "$NEW_PACKAGE" >/dev/null
    test "$(dpkg-query -W ump | awk "{print \$2}")" = "$NEW_VERSION"
    runuser -u ump -- ump --data-dir /var/lib/ump doctor >/dev/null
    find /var/lib/ump -maxdepth 1 -type f \
      \( -name config.json -o -name development-root.der \
         -o -name identity.der -o -name identity-key.der \) \
      -print0 | sort -z | xargs -0 sha256sum >/tmp/identity.upgraded
    cmp /tmp/identity.before /tmp/identity.upgraded
    dpkg --force-downgrade -i "$OLD_PACKAGE" >/dev/null
    test "$(dpkg-query -W ump | awk "{print \$2}")" = "$OLD_VERSION"
    runuser -u ump -- ump --data-dir /var/lib/ump doctor >/dev/null
    find /var/lib/ump -maxdepth 1 -type f \
      \( -name config.json -o -name development-root.der \
         -o -name identity.der -o -name identity-key.der \) \
      -print0 | sort -z | xargs -0 sha256sum >/tmp/identity.rolled-back
    cmp /tmp/identity.before /tmp/identity.rolled-back
    old_fingerprint=$(runuser -u ump -- ump --data-dir /var/lib/ump doctor \
      | sed -n "s/.*\"certificate_fingerprint\":\"\([^\"]*\)\".*/\1/p")
    rotation=$(runuser -u ump -- ump --data-dir /var/lib/ump \
      rotate-development-credentials \
      --confirm-machine-id ump:machine:package-lifecycle)
    grep -q "\"peer_reenrollment_required\":true" <<<"$rotation"
    new_fingerprint=$(runuser -u ump -- ump --data-dir /var/lib/ump doctor \
      | sed -n "s/.*\"certificate_fingerprint\":\"\([^\"]*\)\".*/\1/p")
    test -n "$old_fingerprint"
    test -n "$new_fingerprint"
    test "$old_fingerprint" != "$new_fingerprint"
    sha256sum /var/lib/ump/config.json >/tmp/config.rotated
    cmp /tmp/config.before /tmp/config.rotated
    test -f "/var/lib/ump/credential-archive/$old_fingerprint/identity.der"
    test "$(stat -c %a "/var/lib/ump/credential-archive/$old_fingerprint")" = 700
    test "$(stat -c %a "/var/lib/ump/credential-archive/$old_fingerprint/identity-key.der")" = 600
    dpkg -r ump >/dev/null
    test ! -e /usr/bin/ump
    test -f /var/lib/ump/config.json
    test -f /var/lib/ump/runtime-state.json
    test -f "/var/lib/ump/credential-archive/$old_fingerprint/identity-key.der"
    test "$(stat -c %U /var/lib/ump)" = ump
    test "$(stat -c %a /var/lib/ump)" = 700
  '
echo "Debian upgrade, rollback, and uninstall lifecycle passed: $old_arch"
