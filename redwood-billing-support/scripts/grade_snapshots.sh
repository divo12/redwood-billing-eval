#!/usr/bin/env bash
# Regrade a finished Harbor run outside Harbor (the run itself grades in maintain_r1's verifier): Q00, Q01, Q10, Q11
# on the saved A0 and A1 workspaces, then reward.json (the gate) and scores.json. Use it to regrade invalid cases
# (grade.py --only) or a run graded before Q01 was in Harbor. Same models as the run: SUPPORT_MODEL / USER_MODEL.
#   scripts/grade_snapshots.sh jobs/<job>/<trial> [out_dir]      (provider keys as for the run, e.g. OPENAI_API_KEY)
#   QS="q00 q11" scripts/grade_snapshots.sh ...                  (run only some)
#   WORKERS=1 scripts/grade_snapshots.sh ...                     (conversations in parallel; default 2)
set -euo pipefail
trial=$(cd "${1:?usage: grade_snapshots.sh <trial dir> [out dir]}" && pwd)
out=${2:-$trial/graded}; mkdir -p "$out"; out=$(cd "$out" && pwd)
a0=$trial/steps/build_r0/artifacts/app/workspace
a1=$trial/steps/maintain_r1/artifacts/app/workspace
task=$(cd "$(dirname "$0")/.." && pwd)
for d in "$a0" "$a1"; do test -f "$d/agent.py" || { echo "missing snapshot: $d" >&2; exit 2; }; done

docker build -q -f "$task/environment/Dockerfile" -t redwood-grade \
  --build-arg SUPPORT_MODEL="${SUPPORT_MODEL:-openai/gpt-6-luna}" --build-arg USER_MODEL="${USER_MODEL:-openai/gpt-6-luna}" \
  "$task" >/dev/null
# Graded agents run as `nobody` beside the grader, so the hidden verifier is baked in root-only (never mounted) and
# everything the grader writes is root-only (umask 077, /out 0700).
docker build -q -f "$(dirname "$0")/grader.Dockerfile" -t redwood-grader "$task" >/dev/null
grade() {  # name step workspace [extra grade.py args]
  local name=$1 step=$2 ws=$3; shift 3
  case " ${QS:-q00 q01 q10 q11} " in *" $name "*) ;; *) return 0;; esac
  echo "== $name"
  docker run --rm --entrypoint sh \
    -e OPENAI_API_KEY -e OPENAI_BASE_URL -e ANTHROPIC_API_KEY -e OPENROUTER_API_KEY -e SUPPORT_REASONING_EFFORT \
    -e REDWOOD_CUSTOMER_CACHE=/out/customer-turns.jsonl \
    -v "$ws:/ws:ro" -v "$out:/out" redwood-grader \
    -c 'chmod 700 /out && umask 077 && exec python -m grade.grade "$@"' grade \
    "$step" /ws --out "/out/$name" --workers "${WORKERS:-2}" "$@" | tee "$out/$name.txt" | grep -E "PASS|fail" || true
}
grade q00 build_r0    "$a0"                  # A0 on R0 cases, R0 console
grade q01 maintain_r1 "$a0"                  # A0 on R1 cases, R1 console (the baseline for drift)
grade q10 build_r0    "$a1" --release R1     # A1 on R0 cases, R1 console
grade q11 maintain_r1 "$a1"                  # A1 on R1 cases, R1 console
docker run --rm --entrypoint python \
  -v "$a0:/a0:ro" -v "$a1:/a1:ro" -v "$out:/out" redwood-grader \
  -m grade.final /out /a0 /a1                # writes $out/reward.json and $out/scores.json
