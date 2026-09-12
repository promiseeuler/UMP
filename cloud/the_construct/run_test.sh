#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

environment="$root/.ump-cloud-venv"
if [[ -x "$environment/bin/python" ]]; then
  python_bin="$environment/bin/python"
  export PATH="$environment/bin:$PATH"
else
  if python3 -m venv "$environment" >/dev/null 2>&1; then
    python_bin="$environment/bin/python"
    export PATH="$environment/bin:$PATH"
  else
    python_bin="python3"
    python_user_bin="$(python3 -c 'import site; print(site.USER_BASE)')/bin"
    export PATH="$python_user_bin:$PATH"
    "$python_bin" -m pip install --user --break-system-packages .
  fi
fi
if [[ "$python_bin" != "python3" ]]; then
  "$python_bin" -m pip install .
fi

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_root="${UMP_CLOUD_OUTPUT_ROOT:-$root/.ump-cloud-runs}/$run_id"
mkdir -p "$run_root/runtime"

database="$run_root/inspector.sqlite3"
scenario="$run_root/scenario.json"
content="$run_root/content"
world="$root/gazebo/warehouse.sdf"

if command -v gz >/dev/null 2>&1; then
  backend="gazebo"
  python3 -m ump.gazebo_lab \
    --world "$world" \
    --workspace "$run_root/runtime" \
    --inspector-database "$database" > "$scenario"
else
  backend="headless"
  ump-lab run \
    --workspace "$run_root/runtime" \
    --inspector-database "$database" \
    --output "$scenario" >/dev/null
fi

ump-evidence \
  --database "$database" \
  --scenario-result "$scenario" \
  --output-directory "$content"

printf '\nUMP cloud test passed with backend: %s\n' "$backend"
printf 'Run directory: %s\n' "$run_root"
printf 'Inspector: ump-inspector --database %q --host 0.0.0.0 --port 8765 --allow-remote --auth-token-file TOKEN --tls-certificate CERT --tls-private-key KEY\n' "$database"
printf 'Local/private workspace inspector: ump-inspector --database %q --host 127.0.0.1 --port 8765\n' "$database"
printf 'Content summary: %s\n' "$content/pitch-summary.md"
printf 'Machine evidence: %s\n' "$content/evidence.json"
printf 'Protocol timeline: %s\n' "$content/timeline.csv"
