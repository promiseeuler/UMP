#!/usr/bin/env bash
set -euo pipefail

root=${UMP_S4_ROOT:-/run/ump}
log_dir=${UMP_S4_LOG_DIR:-/tmp/ump-s4}
mkdir -p "$log_dir"
/workspaces/ump_ros2/s4/setup.sh >/dev/null

UMP_DATA_DIR="$root/mobile" umpd >"$log_dir/mobile-umpd.log" 2>&1 &
mobile_pid=$!
UMP_DATA_DIR="$root/arm" umpd >"$log_dir/arm-umpd.log" 2>&1 &
arm_pid=$!
UMP_DATA_DIR="$root/zone" umpd >"$log_dir/zone-umpd.log" 2>&1 &
zone_pid=$!
ros2 launch ump_gazebo s4_stack.launch.py >"$log_dir/ros.log" 2>&1 &
ros_pid=$!

cleanup() {
  if test -n "${performance_pid:-}"; then
    kill "$performance_pid" 2>/dev/null || true
    wait "$performance_pid" 2>/dev/null || true
  fi
  kill "$ros_pid" "$mobile_pid" "$arm_pid" "$zone_pid" 2>/dev/null || true
  wait "$ros_pid" "$mobile_pid" "$arm_pid" "$zone_pid" 2>/dev/null || true
}
trap cleanup EXIT

for socket in "$root/mobile/adapter.sock" "$root/arm/adapter.sock" "$root/zone/adapter.sock"; do
  for _ in $(seq 1 100); do
    test -S "$socket" && break
    sleep 0.1
  done
  test -S "$socket"
done
sleep 5
performance_samples=${UMP_S4_PERFORMANCE_SAMPLES:-$log_dir/performance-samples.jsonl}
performance_report=${UMP_S4_PERFORMANCE_REPORT:-$log_dir/performance.json}
python3 /workspaces/ump_ros2/s4/performance.py sample \
  --output "$performance_samples" --process "mobile=$mobile_pid" \
  --process "arm=$arm_pid" --process "zone=$zone_pid" --interval-ms 100 &
performance_pid=$!
/workspaces/ump_ros2/s4/mission.sh
kill -TERM "$performance_pid"
wait "$performance_pid"
unset performance_pid
python3 /workspaces/ump_ros2/s4/performance.py report \
  --samples "$performance_samples" --invariants "${UMP_S4_REPORT:-/tmp/ump-s4-trace-invariants.json}" \
  --output "$performance_report"
