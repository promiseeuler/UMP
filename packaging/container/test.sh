#!/usr/bin/env bash
set -euo pipefail

image=${1:-ump:local}
volume="ump-native-test-$$"
peer_volume="ump-native-peer-test-$$"
exchange_volume="ump-native-exchange-test-$$"
network="ump-native-test-$$"
container="ump-native-test-$$"
cleanup() {
  status=$?
  if test "$status" -ne 0; then
    docker logs "$container" >&2 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm -f "$volume" >/dev/null 2>&1 || true
  docker volume rm -f "$peer_volume" "$exchange_volume" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker volume create "$volume" >/dev/null

identity=$(docker run --rm --entrypoint /usr/bin/id "$image" -u)
test "$identity" = 65532
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" \
  --data-dir /var/lib/ump init \
  --machine-id ump:machine:container-test \
  --machine-class test_fixture \
  --listen 0.0.0.0:7443 >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" \
  --data-dir /var/lib/ump doctor >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" \
  --data-dir /var/lib/ump run --once >/dev/null
old_fingerprint=$(docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" --data-dir /var/lib/ump doctor \
  | sed -n 's/.*"certificate_fingerprint":"\([^"]*\)".*/\1/p')
rotation=$(docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" --data-dir /var/lib/ump \
  rotate-development-credentials --confirm-machine-id ump:machine:container-test)
grep -q '"peer_reenrollment_required":true' <<<"$rotation"
new_fingerprint=$(docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" "$image" --data-dir /var/lib/ump doctor \
  | sed -n 's/.*"certificate_fingerprint":"\([^"]*\)".*/\1/p')
test -n "$old_fingerprint"
test -n "$new_fingerprint"
test "$old_fingerprint" != "$new_fingerprint"
docker run --rm --entrypoint /bin/sh -v "$volume:/var/lib/ump" "$image" \
  -c "test -f /var/lib/ump/config.json \
    && test \"\$(stat -c %u /var/lib/ump/config.json)\" = 65532 \
    && test \"\$(stat -c %a /var/lib/ump/credential-archive/$old_fingerprint)\" = 700 \
    && test \"\$(stat -c %a /var/lib/ump/credential-archive/$old_fingerprint/identity-key.der)\" = 600"
docker run -d --name "$container" -v "$volume:/var/lib/ump" "$image" >/dev/null
for _ in $(seq 1 20); do
  test "$(docker inspect -f '{{.State.Running}}' "$container")" = true && break
  sleep 0.1
done
test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
docker stop -t 2 "$container" >/dev/null
docker start "$container" >/dev/null
sleep 0.2
test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
docker rm -f "$container" >/dev/null

docker volume create "$peer_volume" >/dev/null
docker volume create "$exchange_volume" >/dev/null
docker network create "$network" >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$peer_volume:/var/lib/ump" "$image" \
  --data-dir /var/lib/ump init \
  --machine-id ump:machine:container-peer \
  --machine-class test_fixture \
  --dns-name container-peer.ump.local \
  --listen 0.0.0.0:7443 >/dev/null
docker run --rm --user 0:0 --entrypoint /bin/sh \
  -v "$volume:/coordinator:ro" -v "$peer_volume:/peer:ro" \
  -v "$exchange_volume:/exchange" "$image" -c \
  'cp /coordinator/identity.der /coordinator/development-root.der /exchange/; cp /peer/identity.der /exchange/peer-identity.der; cp /peer/development-root.der /exchange/peer-root.der'
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$peer_volume:/var/lib/ump" -v "$exchange_volume:/exchange:ro" "$image" \
  --data-dir /var/lib/ump trust \
  --machine-id ump:machine:container-test \
  --certificate /exchange/identity.der \
  --root-certificate /exchange/development-root.der --allow-tasks >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$volume:/var/lib/ump" -v "$exchange_volume:/exchange:ro" "$image" \
  --data-dir /var/lib/ump trust \
  --machine-id ump:machine:container-peer \
  --certificate /exchange/peer-identity.der \
  --root-certificate /exchange/peer-root.der --allow-metadata >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$peer_volume:/var/lib/ump" "$image" --data-dir /var/lib/ump \
  register-capability --capability org.ump.test.echo --interruptible >/dev/null
docker run --rm --entrypoint /usr/local/bin/ump \
  -v "$peer_volume:/var/lib/ump" "$image" --data-dir /var/lib/ump \
  grant-lease --lease-id container-test-lease \
  --holder ump:machine:container-test --capability org.ump.test.echo \
  --duration-ms 60000 >/dev/null
docker run -d --name "$container" --network "$network" \
  -v "$peer_volume:/var/lib/ump" "$image" >/dev/null
peer_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")
client_network=(--network "$network")
if test "$(uname -s)" = Darwin; then
  peer_ip=127.0.0.1
  client_network=(--network "container:$container")
fi
response=$(docker run --rm "${client_network[@]}" \
  --entrypoint /usr/local/bin/ump -v "$volume:/var/lib/ump" "$image" \
  --data-dir /var/lib/ump issue-task \
  --target "$peer_ip:7443" --target-name container-peer.ump.local \
  --target-machine ump:machine:container-peer --task-id container-network-task \
  --capability org.ump.test.echo --input-json '{"message":"hello"}' \
  --lease-id container-test-lease --idempotency-key container-network-task)
grep -q '"status":"accepted"' <<<"$response"
echo "Rootless container lifecycle passed: $image"
