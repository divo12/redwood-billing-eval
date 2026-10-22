#!/usr/bin/env bash
# Quality gate for the Redwood billing-support task code: lint, strict types, complexity <= 10.
set -euo pipefail
cd "$(dirname "$0")/.."
TASK=tasks/redwood-devsupport
PATHS=("$TASK/tests" "$TASK/environment/redwood_kit" "$TASK/environment/advance.py")

uvx ruff@0.16.9 check "${PATHS[@]}"
uv run --python 3.12 --no-project --with mypy==2.3.1 --with pydantic==2.13.5 --with 'openai>=1.100,<3' --with 'anthropic>=0.60,<1' --with 'fastembed==0.9.0' \
    mypy "$TASK/tests" "$TASK/environment/redwood_kit" "$TASK/environment/advance.py"
uvx xenon@0.9.3 --max-absolute B --max-modules B --max-average A "${PATHS[@]}"
if rg -n '\b(getattr|setattr)\(' "${PATHS[@]}"; then
    echo "getattr/setattr are not allowed" >&2
    exit 1
fi
