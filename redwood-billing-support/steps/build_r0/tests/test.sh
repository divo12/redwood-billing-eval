#!/bin/sh
# Score A0, then reveal R1 for maintain_r1.
python /tests/verify.py build_r0
status=$?
/usr/local/libexec/redwood-advance
advance_status=$?
# Hand files to the mount owner (host user) so Harbor can archive them.
chown -R --reference=/logs/verifier /logs/verifier
[ "$advance_status" -eq 0 ] || exit 1
exit $status
