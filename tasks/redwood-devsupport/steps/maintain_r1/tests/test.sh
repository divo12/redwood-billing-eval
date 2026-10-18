#!/bin/sh
python /tests/verify.py maintain_r1
status=$?
# Hand files to the mount owner (host user) so Harbor can archive them.
chown -R --reference=/logs/verifier /logs/verifier
exit "$status"
