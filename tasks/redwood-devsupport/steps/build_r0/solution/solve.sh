#!/bin/sh
# Oracle: install the R0 reference agent, build its evidence index, and record the Pi session.
set -eu
cp -R /solution/workspace/. /app/workspace/
python /app/workspace/build_index.py
if [ "$(id -u)" -eq 0 ]; then
  chown -R harbor:harbor /app/workspace
fi
mkdir -p /logs/agent/pi/sessions
printf '%s\n' '{"type":"session","version":3,"id":"oracle","cwd":"/app"}' > /logs/agent/pi/sessions/oracle.jsonl
