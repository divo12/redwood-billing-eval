#!/bin/sh
set -eu
umask 077
python -m redwood_kit.billing.store --serve 2>>/logs/verifier/practice-console.log &
for sock in practice-console; do
  i=0
  while [ ! -S "/run/redwood/$sock.sock" ]; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then
      echo "$sock service did not start" >&2
      exit 1
    fi
    sleep 0.1
  done
done
exec "$@"
