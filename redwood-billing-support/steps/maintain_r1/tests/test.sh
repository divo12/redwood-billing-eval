#!/bin/sh
# Check A1's deliverables and freeze it, then grade both frozen workspaces (A0 from build_r0, A1 now) on the
# held-out conversations and write the run's reward (tests/grade/final.py): the gate over Q00, Q10 and Q11, plus
# drift (needs Q01), regression, behaviour_robustness, and this step's artifact_quality.
python /tests/verify.py maintain_r1
status=$?
if [ "$status" -eq 0 ]; then
  # Graded agents run as `nobody` beside the grader: nothing hidden may be readable to them.
  chmod -R go-rwx /tests
  umask 077
  out=/logs/verifier/graded
  mkdir -p "$out"
  export REDWOOD_CUSTOMER_CACHE="$out/customer-turns.jsonl"
  w=${GRADE_WORKERS:-4}
  cd /tests || exit 2
  python -m grade.grade build_r0 /run/redwood-agent-R0 --out "$out/q00" --workers "$w" > "$out/q00.txt" &&
    python -m grade.grade maintain_r1 /run/redwood-agent-R0 --out "$out/q01" --workers "$w" > "$out/q01.txt" &&
    python -m grade.grade build_r0 /run/redwood-agent-R1 --release R1 --out "$out/q10" --workers "$w" > "$out/q10.txt" &&
    python -m grade.grade maintain_r1 /run/redwood-agent-R1 --out "$out/q11" --workers "$w" > "$out/q11.txt" &&
    python -m grade.final "$out" /run/redwood-agent-R0 /run/redwood-agent-R1 &&
    python -c 'import json, sys; a, b = (json.load(open(p)) for p in sys.argv[1:]); json.dump({**b, "artifact_quality": a["artifact_quality"]}, open(sys.argv[1], "w"), indent=2)' \
      /logs/verifier/reward.json "$out/reward.json"
  status=$?
fi
# Hand files to the mount owner (host user) so Harbor can archive them.
chown -R --reference=/logs/verifier /logs/verifier
exit "$status"
