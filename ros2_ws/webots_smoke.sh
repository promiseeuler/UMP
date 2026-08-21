#!/usr/bin/env bash
set -euo pipefail

: "${ROS_DISTRO:?Source ROS 2 before running the Webots smoke test}"
export USER=${USER:-ump}
launch_log=/tmp/ump-webots.log
report=${UMP_WEBOTS_REPORT:-/tmp/ump-ros2-webots-smoke.json}
ros2 launch ump_webots_demo warehouse.launch.py mode:=fast autostart:=false >"$launch_log" 2>&1 &
launch_pid=$!
cleanup() {
  kill "$launch_pid" 2>/dev/null || true
  wait "$launch_pid" 2>/dev/null || true
}
trap cleanup EXIT

ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$launch_pid" 2>/dev/null; then
    cat "$launch_log" >&2
    exit 1
  fi
  actions=$(timeout 1s ros2 action list 2>/dev/null || true)
  if [[ $(grep -c execute_capability <<<"$actions" || true) -eq 3 ]]; then
    ready=true
    break
  fi
  sleep 1
done
if [[ $ready != true ]]; then
  cat "$launch_log" >&2
  exit 1
fi

share=$(ros2 pkg prefix ump_webots_demo --share)
ros2 run ump_webots_demo warehouse_smoke_client \
  --report "$report" --world "$share/worlds/ump_warehouse.wbt"
test -s "$report"
