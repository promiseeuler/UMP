#!/usr/bin/env bash
set -euo pipefail

root=${UMP_S4_ROOT:-/run/ump}
output=${UMP_S4_INSPECTOR_DIR:-/tmp/ump-s4-inspector}
mkdir -p "$output"

for machine in coordinator mobile arm zone; do
  ump --data-dir "$root/$machine" inspector \
    --output "$output/$machine.json" >/dev/null
done

python3 "$(dirname "$0")/verify_inspector.py" "$output" \
  --profile "${UMP_S4_INSPECTOR_PROFILE:-nominal}" \
  --report "${UMP_S4_INSPECTOR_REPORT:-$output/report.json}"

echo "S4 inspector bundle written to $output"
