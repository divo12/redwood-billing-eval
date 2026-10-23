#!/usr/bin/env bash
# Score a finished Harbor run: grade Q00, Q01, Q10, Q11 on the saved A0 and A1 workspaces, then write
# reward.json (the gate) and scores.json (pass rates, drift, regression, health). This is the only grading path.
#   scripts/grade_snapshots.sh jobs/<job>/<trial> [out_dir]      (needs ANTHROPIC_API_KEY, or REDWOOD_SUPPORT_OVERRIDE)
#   QS="q00 q11" scripts/grade_snapshots.sh ...                  (run only some; each Q is 10 conversations)
#   WORKERS=1 scripts/grade_snapshots.sh ...                     (conversations in parallel; default 8, lower it under a rate limit)
#   REDWOOD_CUSTOMER_MODEL=gpt-6-luna scripts/grade_snapshots.sh ...   (a model words the scripted developer's lines)
set -euo pipefail
trial=$(cd "${1:?usage: grade_snapshots.sh <trial dir> [out dir]}" && pwd)
out=${2:-$trial/graded}; mkdir -p "$out"; out=$(cd "$out" && pwd)
a0=$trial/steps/build_r0/artifacts/app/workspace
a1=$trial/steps/maintain_r1/artifacts/app/workspace
task=$(cd "$(dirname "$0")/../tasks/redwood-devsupport" && pwd)
for d in "$a0" "$a1"; do test -f "$d/agent.py" || { echo "missing snapshot: $d" >&2; exit 2; }; done

docker build -q -f "$task/environment/Dockerfile" -t redwood-grade "$task" >/dev/null
grade() {  # name step workspace [extra grade.py args]
  local name=$1 step=$2 ws=$3; shift 3
  case " ${QS:-q00 q01 q10 q11} " in *" $name "*) ;; *) return 0;; esac
  echo "== $name"
  docker run --rm --entrypoint python -w /task/tests \
    -e ANTHROPIC_API_KEY -e REDWOOD_SUPPORT_OVERRIDE -e REDWOOD_OPENAI_REASONING_EFFORT -e OPENAI_API_KEY -e OPENAI_BASE_URL \
    -e REDWOOD_CUSTOMER_MODEL -e REDWOOD_CUSTOMER_CACHE=/out/customer-turns.jsonl \
    -v "$task:/task:ro" -v "$ws:/ws:ro" -v "$out:/out" redwood-grade \
    -m grade.grade "$step" /ws --out "/out/$name" --workers "${WORKERS:-8}" "$@" | tee "$out/$name.txt" | grep -E "PASS|fail" || true
}
grade q00 build_r0    "$a0"                  # A0 on R0 cases, R0 console
grade q01 maintain_r1 "$a0"                  # A0 on R1 cases, R1 console
grade q10 build_r0    "$a1" --release R1     # A1 on R0 cases, R1 console
grade q11 maintain_r1 "$a1"                  # A1 on R1 cases, R1 console
docker run --rm --entrypoint python -w /task/tests \
  -v "$task:/task:ro" -v "$a0:/a0:ro" -v "$a1:/a1:ro" -v "$out:/out" redwood-grade \
  -m grade.final /out /a0 /a1                # writes $out/reward.json and $out/scores.json
