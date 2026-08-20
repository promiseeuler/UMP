#!/usr/bin/env bash
set -euo pipefail

root=${UMP_S4_ROOT:-/run/ump}
lease_duration_ms=${UMP_S4_LEASE_DURATION_MS:-600000}
arm_protocol_minor=${UMP_S4_ARM_PROTOCOL_MINOR:-2}
coordinator="$root/coordinator"
mobile="$root/mobile"
arm="$root/arm"
zone="$root/zone"

mkdir -p "$coordinator" "$mobile" "$arm" "$zone"
ump --data-dir "$coordinator" init --force \
  --machine-id ump:machine:s4-coordinator --machine-class coordinator \
  --dns-name coordinator.ump.local --listen 127.0.0.1:17440 >/dev/null
ump --data-dir "$mobile" init --force \
  --machine-id ump:machine:mobile-base-1 --machine-class mobile_base \
  --dns-name mobile.ump.local --listen 127.0.0.1:17441 >/dev/null
ump --data-dir "$arm" init --force \
  --machine-id ump:machine:robot-arm-1 --machine-class robot_arm \
  --dns-name arm.ump.local --listen 127.0.0.1:17442 \
  --protocol-minor "$arm_protocol_minor" >/dev/null
ump --data-dir "$zone" init --force \
  --machine-id ump:machine:transfer-zone-1 --machine-class zone_sensor \
  --dns-name zone.ump.local --listen 127.0.0.1:17443 >/dev/null

for executor in "$mobile" "$arm"; do
  ump --data-dir "$executor" trust \
    --machine-id ump:machine:s4-coordinator \
    --certificate "$coordinator/identity.der" \
    --root-certificate "$coordinator/development-root.der" \
    --allow-metadata --allow-tasks --allow-coordination >/dev/null
done
for participant in "$mobile" "$arm"; do
  participant_id=$(ump --data-dir "$participant" doctor | sed -n 's/.*"machine_id":"\([^"]*\)".*/\1/p')
  ump --data-dir "$zone" trust \
    --machine-id "$participant_id" \
    --certificate "$participant/identity.der" \
    --root-certificate "$participant/development-root.der" \
    --allow-metadata --allow-coordination >/dev/null
  ump --data-dir "$participant" trust \
    --machine-id ump:machine:transfer-zone-1 \
    --certificate "$zone/identity.der" \
    --root-certificate "$zone/development-root.der" --allow-metadata >/dev/null
done
ump --data-dir "$coordinator" trust \
  --machine-id ump:machine:mobile-base-1 \
  --certificate "$mobile/identity.der" \
  --root-certificate "$mobile/development-root.der" --allow-metadata >/dev/null
ump --data-dir "$coordinator" trust \
  --machine-id ump:machine:robot-arm-1 \
  --certificate "$arm/identity.der" \
  --root-certificate "$arm/development-root.der" --allow-metadata >/dev/null

for capability in org.ump.navigation.navigate org.ump.payload.attach org.ump.payload.detach; do
  ump --data-dir "$mobile" register-capability --capability "$capability" --interruptible >/dev/null
done
for capability in org.ump.manipulation.arm_pose org.ump.payload.attach org.ump.payload.detach; do
  ump --data-dir "$arm" register-capability --capability "$capability" --interruptible >/dev/null
done

ump --data-dir "$mobile" grant-lease --lease-id lease-s4-mobile \
  --holder ump:machine:s4-coordinator \
  --capability org.ump.navigation.navigate \
  --capability org.ump.payload.attach --capability org.ump.payload.detach \
  --duration-ms "$lease_duration_ms" >/dev/null
ump --data-dir "$arm" grant-lease --lease-id lease-s4-arm \
  --holder ump:machine:s4-coordinator \
  --capability org.ump.manipulation.arm_pose \
  --capability org.ump.payload.attach --capability org.ump.payload.detach \
  --duration-ms "$lease_duration_ms" >/dev/null
ump --data-dir "$zone" register-resource \
  --resource-id ump:resource:s4-transfer-zone \
  --resource-type org.ump.resource.transfer_zone \
  --concurrency exclusive --frame-id world >/dev/null

echo "$root"
