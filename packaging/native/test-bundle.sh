#!/usr/bin/env bash
set -euo pipefail

bundle=${1:?usage: test-bundle.sh BUNDLE.tar.gz}
test -f "$bundle"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
tar -xzf "$bundle" -C "$root"
bundle_root=$(find "$root" -mindepth 1 -maxdepth 1 -type d | head -1)
test -n "$bundle_root"
(cd "$bundle_root" && sha256sum -c SHA256SUMS >/dev/null)
test -x "$bundle_root/bin/ump"
test -x "$bundle_root/bin/umpd"
test -f "$bundle_root/systemd/umpd.service"
test -f "$bundle_root/manifest.json"
"$bundle_root/bin/ump" --version >/dev/null
python3 - "$bundle_root/manifest.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert manifest["artifact"] == "ump-standalone-linux"
assert manifest["architecture"] in {"amd64", "arm64"}
assert manifest["version"]
assert len(manifest["binaries"]["ump"]["sha256"]) == 64
assert len(manifest["binaries"]["umpd"]["sha256"]) == 64
PY
echo "Standalone bundle validation passed: $bundle"
