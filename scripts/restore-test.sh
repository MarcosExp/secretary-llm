#!/bin/sh
# Backup round-trip test: write a probe row, let Litestream replicate it,
# restore the database from the bucket and verify the restored copy.
# Run from the repo root with the stack up. Extra compose flags go in COMPOSE,
# e.g. COMPOSE="docker compose -f docker-compose.yml -f compose.tailscale.yml" scripts/restore-test.sh
set -eu
# Git Bash on Windows would rewrite /data/... into a host path; no effect elsewhere.
export MSYS_NO_PATHCONV=1

compose=${COMPOSE:-docker compose}
wait_s=${RESTORE_WAIT:-15}
restored=/data/restore-test.db

token=$($compose exec -T app python -m app.ops.restore_check write | tr -d '\r')
[ -n "$token" ] || { echo "restore test: could not write the probe row" >&2; exit 1; }
echo "probe written: $token"

echo "waiting ${wait_s}s for replication..."
sleep "$wait_s"

$compose exec -T app rm -f "$restored" "$restored-shm" "$restored-wal"
$compose exec -T litestream litestream restore -o "$restored" /data/secretary.db

status=0
$compose exec -T app python -m app.ops.restore_check verify "$restored" "$token" || status=$?
$compose exec -T app rm -f "$restored" "$restored-shm" "$restored-wal"

if [ "$status" -eq 0 ]; then
  echo "restore test: PASSED"
else
  echo "restore test: FAILED" >&2
fi
exit "$status"
