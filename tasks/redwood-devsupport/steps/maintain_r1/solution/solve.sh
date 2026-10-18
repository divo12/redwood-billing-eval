#!/bin/sh
# Oracle: overlay the R1 console rule, rebuild evidence across R0 and R1, and resume the same Pi session.
# /logs/agent only for resumed agents, so the oracle rewrites its session, one line longer.
set -eu
cp -R /solution/workspace/. /app/workspace/
python /app/workspace/build_index.py /app/R
if [ "$(id -u)" -eq 0 ]; then
  chown -R harbor:harbor /app/workspace
fi
mkdir -p /logs/agent/pi/sessions
printf '%s\n' \
  '{"type":"session","version":3,"id":"oracle","cwd":"/app"}' \
  '{"type":"message_end","message":{"role":"user","content":"maintain R1"}}' \
  > /logs/agent/pi/sessions/oracle.jsonl
