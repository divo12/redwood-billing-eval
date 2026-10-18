import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport" / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "environment"))

from grade import grade  # noqa: E402
from run.run_agent import ModelCall  # noqa: E402

MANIFEST = TESTS.parent / "environment" / "app" / "framework" / "deployment_manifest.json"


def _call(prompt, completion):
    return ModelCall(model="gpt-6-sol", kwargs={}, instructions_chars=0, tools=0, prompt_tokens=prompt,
                     completion_tokens=completion, messages=[], response={})


def test_rates_and_budget_mirror_the_published_manifest():
    manifest = json.loads(MANIFEST.read_text())
    rates = {m["model"]: grade.Rate.model_validate(m["credit_rates"]) for m in manifest["allowed_agent_models"]}
    assert rates == grade.CREDIT_RATES
    [requirement] = manifest["performance_requirements"]
    assert requirement["budget"] == grade.CREDIT_BUDGET


def test_credits_price_prompt_and_completion_tokens_per_million():
    assert grade.credits([_call(1_000_000, 0), _call(0, 100_000)]) == 3.0 + 1.5


def test_overage_is_the_fraction_of_the_mean_over_budget():
    assert grade.credit_use([1.0, 1.5], budget=1.0).overage == 0.25
    assert grade.credit_use([0.5, 1.5], budget=1.0).overage == 0.0


def test_no_budget_means_no_penalty():
    use = grade.credit_use([100.0], budget=None)
    assert use.overage == 0.0 and use.mean_credits == 100.0
    assert grade.credit_use([], budget=1.0).mean_credits is None
