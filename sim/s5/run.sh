#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
artifacts=${UMP_S5_ARTIFACTS:-$(mktemp -d)}
mkdir -p "$artifacts"
export PATH="$repo/target/debug:$PATH"
export UMP_EXAMPLE_CONTROLLER_TOKEN=s5-development-only
export UMP_VENDOR_CONTROLLER_TOKEN=$UMP_EXAMPLE_CONTROLLER_TOKEN
use_ros_zone=${UMP_S5_USE_ROS_ZONE:-false}

python3 "$repo/bridge/http/example_controller.py" --port 18080 >"$artifacts/controller.log" 2>&1 &
controller_pid=$!

cleanup() {
  kill "$controller_pid" 2>/dev/null || true
  wait "$controller_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 100); do
  python3 - <<'PY' >/dev/null 2>&1 && break || true
import socket
with socket.create_connection(("127.0.0.1", 18080), timeout=0.1):
    pass
PY
  sleep 0.02
done

run_case() {
  local mode=$1 root="$artifacts/$1"
  mkdir -p "$root"
  export UMP_S4_ROOT="$root/runtime"
  export UMP_S4_TRACE="$root/trace.jsonl"
  export UMP_S4_REPORT="$root/invariants.json"
  export UMP_S4_INSPECTOR_DIR="$root/inspector"
  "$repo/sim/s4/setup.sh" >/dev/null

  cat >"$root/bridge.json" <<JSON
{
  "ump_socket": "$root/runtime/arm/adapter.sock",
  "controller_base_url": "http://127.0.0.1:18080",
  "token_env": "UMP_VENDOR_CONTROLLER_TOKEN",
  "allow_insecure_loopback": true,
  "read_only": false,
  "poll_interval_seconds": 0.02,
  "health_path": "/v1/health",
  "capabilities": {
    "org.ump.manipulation.arm_pose": {"path":"/v1/commands/execute","cancel_path":"/v1/commands/cancel"},
    "org.ump.payload.attach": {"path":"/v1/commands/execute","cancel_path":"/v1/commands/cancel"},
    "org.ump.payload.detach": {"path":"/v1/commands/execute","cancel_path":"/v1/commands/cancel"}
  }
}
JSON
  if [[ $mode == gateway ]]; then
    cat >"$root/gateway.json" <<JSON
{
  "gateway_id": "ump:gateway:s5-arm-01",
  "represented_machine_id": "ump:machine:robot-arm-1",
  "controller_interface": "example_https_api_v1",
  "runtime_config": "$root/runtime/arm/config.json",
  "bridge_config": "$root/bridge.json",
  "status_file": "$root/gateway-status.json"
}
JSON
    python3 "$repo/gateway/ump_gateway.py" --config "$root/gateway.json" --configure-runtime
  fi

  UMP_DATA_DIR="$root/runtime/mobile" umpd >"$root/mobile.log" 2>&1 & local mobile_pid=$!
  UMP_DATA_DIR="$root/runtime/arm" umpd >"$root/arm.log" 2>&1 & local arm_pid=$!
  UMP_DATA_DIR="$root/runtime/zone" umpd >"$root/zone.log" 2>&1 & local zone_pid=$!
  local mobile_adapter_pid arm_adapter_pid zone_adapter_pid
  case_cleanup() {
    kill "$mobile_pid" "$arm_pid" "$zone_pid" "${mobile_adapter_pid:-}" "${arm_adapter_pid:-}" "${zone_adapter_pid:-}" 2>/dev/null || true
    wait "$mobile_pid" "$arm_pid" "$zone_pid" "${mobile_adapter_pid:-}" "${arm_adapter_pid:-}" "${zone_adapter_pid:-}" 2>/dev/null || true
  }
  trap 'case_cleanup; cleanup' EXIT
  for socket in "$root/runtime/mobile/adapter.sock" "$root/runtime/arm/adapter.sock"; do
    for _ in $(seq 1 100); do test -S "$socket" && break; sleep 0.02; done
    test -S "$socket"
  done
  python3 "$repo/sim/s4/fake_adapter.py" "$root/runtime/mobile/adapter.sock" 3 &
  mobile_adapter_pid=$!
  if [[ $use_ros_zone == true ]]; then
    for _ in $(seq 1 100); do
      test -S "$root/runtime/zone/adapter.sock" && break
      sleep 0.02
    done
    test -S "$root/runtime/zone/adapter.sock"
    ros2 run ump_ros2_adapter adapter --ros-args \
      -p machine_id:=ump:machine:transfer-zone-1 \
      -p local_socket:="$root/runtime/zone/adapter.sock" \
      -p parent_frame:=world -p child_frame:=transfer_zone >"$root/zone-ros.log" 2>&1 &
    zone_adapter_pid=$!
    for _ in $(seq 1 100); do
      ros2 lifecycle set /ump_ros2_adapter configure >/dev/null 2>&1 && break
      sleep 0.05
    done
    ros2 lifecycle set /ump_ros2_adapter activate >/dev/null
  fi
  if [[ $mode == gateway ]]; then
    python3 "$repo/gateway/ump_gateway.py" --config "$root/gateway.json" &
  else
    python3 "$repo/bridge/http/ump_http_bridge.py" --config "$root/bridge.json" &
  fi
  arm_adapter_pid=$!
  "$repo/sim/s4/mission.sh" >/dev/null
  wait "$mobile_adapter_pid"
  python3 "$repo/sim/s4/local_api_call.py" "$root/runtime/arm/adapter.sock" ping '{}' >"$root/ping.json"
  ump --data-dir "$root/runtime/arm" inspector --output "$root/arm-inspector.json" >/dev/null
  if [[ $use_ros_zone == true ]]; then
    python3 "$repo/sim/s4/local_api_call.py" "$root/runtime/zone/adapter.sock" ping '{}' >"$root/zone-ping.json"
  fi
  if [[ $mode == gateway ]]; then
    local revision_before_restart disconnected=false recovered=false restarted=false
    revision_before_restart=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$root/gateway-status.json")
    kill "$controller_pid"
    wait "$controller_pid" 2>/dev/null || true
    for _ in $(seq 1 150); do
      if grep -q '"connectivity": "controller_disconnected"' "$root/gateway-status.json"; then
        disconnected=true
        break
      fi
      sleep 0.02
    done
    $disconnected
    python3 "$repo/bridge/http/example_controller.py" --port 18080 >"$artifacts/controller-restarted.log" 2>&1 &
    controller_pid=$!
    for _ in $(seq 1 150); do
      if grep -q '"connectivity": "connected"' "$root/gateway-status.json"; then
        recovered=true
        break
      fi
      sleep 0.02
    done
    $recovered
    kill "$arm_adapter_pid"
    wait "$arm_adapter_pid" 2>/dev/null || true
    python3 "$repo/gateway/ump_gateway.py" --config "$root/gateway.json" &
    arm_adapter_pid=$!
    for _ in $(seq 1 150); do
      local current_revision
      current_revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$root/gateway-status.json")
      if (( current_revision > revision_before_restart )) && grep -q '"connectivity": "connected"' "$root/gateway-status.json"; then
        restarted=true
        break
      fi
      sleep 0.02
    done
    $restarted
    printf '{"controller_disconnect_observed":true,"controller_recovery_observed":true,"gateway_restart_advanced_revision":true}\n' >"$root/fault-report.json"
  fi
  case_cleanup
  trap cleanup EXIT
}

run_case direct
run_case gateway
verify_args=(
  --direct-report "$artifacts/direct/invariants.json" \
  --gateway-report "$artifacts/gateway/invariants.json" \
  --direct-ping "$artifacts/direct/ping.json" \
  --gateway-ping "$artifacts/gateway/ping.json" \
  --gateway-status "$artifacts/gateway/gateway-status.json" \
  --gateway-inspector "$artifacts/gateway/arm-inspector.json" \
  --fault-report "$artifacts/gateway/fault-report.json" \
  --output "$artifacts/report.json"
)
if [[ $use_ros_zone == true ]]; then
  ros_prefix=$(ros2 pkg prefix ump_ros2_adapter)
  direct_revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state_revision"])' "$artifacts/direct/zone-ping.json")
  gateway_revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state_revision"])' "$artifacts/gateway/zone-ping.json")
  python3 - "$artifacts/ros-evidence.json" "$ros_prefix" "$direct_revision" "$gateway_revision" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "package_prefix": sys.argv[2],
    "direct_state_revision": int(sys.argv[3]),
    "gateway_state_revision": int(sys.argv[4]),
}, indent=2, sort_keys=True) + "\n")
PY
  verify_args+=(--ros-evidence "$artifacts/ros-evidence.json")
fi
python3 "$repo/sim/s5/verify.py" "${verify_args[@]}"
echo "S5 protocol checkpoint passed; artifacts: $artifacts"
