"""The R1 reference workspace (build_r0 solution with maintain_r1 overlaid) on the R1 practice console."""
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path.insert(0, str(TASK / "environment"))

from redwood_kit.billing.store import local_console  # noqa: E402
from redwood_kit.developer import Developer  # noqa: E402
from redwood_kit.hooks import ToolRunner  # noqa: E402
from redwood_kit.messages import ToolCall  # noqa: E402


def _load(workspace: Path, name: str):
    spec = importlib.util.spec_from_file_location(f"r1_oracle_{name}", workspace / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def oracle(tmp_path):
    workspace = tmp_path / "workspace"
    shutil.copytree(TASK / "steps" / "build_r0" / "solution" / "workspace", workspace,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(TASK / "steps" / "maintain_r1" / "solution" / "workspace", workspace, dirs_exist_ok=True)
    previous = sys.modules.pop("billing_rules", None)
    sys.path.insert(0, str(workspace))
    try:
        tools_module, hooks_module = _load(workspace, "tools"), _load(workspace, "hooks")
    finally:
        sys.path.remove(str(workspace))
        sys.modules.pop("billing_rules", None)
        if previous is not None:
            sys.modules["billing_rules"] = previous
    tools = tools_module.Tools()
    tools.billing = local_console("R1", TASK / "environment/r1/development_seed.json")
    answers = iter(["no", "yes", "yes", "yes"])  # the developer's answers to successive confirmations
    tools.developer = Developer(lambda request: {"answer": next(answers), "message": ""}
                                if request["op"] == "confirm" else {"answers": {}})
    hooks = hooks_module.Hooks()
    runner = ToolRunner(tools, hooks)
    counter = iter(range(1, 1000))
    return runner, hooks_module, lambda name, args: runner.run(ToolCall(str(next(counter)), name, args))


def _credit_args():
    return {"account_id": "practice-alderwick", "invoice_id": "PRACTICE-DUP-180", "line_id": "PRACTICE-DUP-180/L1"}


def test_credit_waits_for_an_explicit_yes(oracle):
    runner, _, call = oracle
    runner.user_message("I was charged twice on PRACTICE-DUP-180")
    assert not call("find_account", {"account_id": "practice-alderwick"}).error
    blocked = call("credit_duplicate", _credit_args())
    assert blocked.error and "confirm_with_developer" in blocked.content
    runner.user_message("Yes, go ahead")  # consent in chat text does not count
    assert call("credit_duplicate", _credit_args()).error
    assert json.loads(call("confirm_with_developer", {"proposal": "Credit $180 on L1"}).content)["answer"] == "no"
    assert call("credit_duplicate", _credit_args()).error
    assert json.loads(call("confirm_with_developer", {"proposal": "Credit $180 on L1"}).content)["answer"] == "yes"
    credited = call("credit_duplicate", _credit_args())
    assert not credited.error, credited.content
    assert json.loads(credited.content)["amount_credited"] == "$180.00"


def test_contract_account_gets_a_dispute_not_a_credit(oracle):
    runner, _, call = oracle
    runner.user_message("PRACTICE-R1-DUP-60 bills one request twice")
    call("confirm_with_developer", {"proposal": "skip"})  # "no"
    call("confirm_with_developer", {"proposal": "Open a dispute on PRACTICE-R1-DUP-60/L2"})  # "yes"
    assert not call("find_account", {"account_id": "practice-ironbark"}).error
    args = {"account_id": "practice-ironbark", "invoice_id": "PRACTICE-R1-DUP-60", "line_id": "PRACTICE-R1-DUP-60/L2"}
    credit = call("credit_duplicate", args)
    assert credit.error and "signed contract" in credit.content
    dispute = call("open_dispute", args)
    assert not dispute.error, dispute.content
    assert json.loads(dispute.content)["reason"] == "duplicate_charge"


def test_credit_balances_come_from_the_ledger(oracle):
    _, _, call = oracle
    result = json.loads(call("credit_balances", {"account_id": "practice-juniper"}).content)
    assert result["source"] == "credit_ledger"
    assert [credit["balance"] for credit in result["credits"]] == ["$120.00"]


@pytest.mark.parametrize("text", [
    "my card is 4242424242424242",
    "my card is 4242 4242 4242 4242",
    "my card is four two four two four two four two four two four two four two four two",
])
def test_card_numbers_never_reach_the_model_or_a_write(oracle, text):
    runner, hooks_module, _ = oracle
    assert "4242" not in runner.user_message(text) and "four two four two" not in runner.user_message(text)
    nested = hooks_module._redacted({"details": {"note": text}, "lines": [text], "n": 3})
    assert hooks_module.CARD_REMOVED in nested["details"]["note"] and nested["n"] == 3
    assert "4242" not in json.dumps(nested) and "four two four two" not in json.dumps(nested)


def test_oracle_workspaces_hold_no_scenario_identifiers():
    data = json.loads((TASK / "tests/data/billing_scenarios.json").read_text())
    text = json.dumps(data["scenarios"])
    identifiers = set(re.findall(r"\b(?:INV-[\w-]+|req_\w+|sub-\d+)", text))
    identifiers |= set(re.findall(r'"account_id": "([\w-]+)"', text))
    identifiers |= set(re.findall(r"[Yy]our org is (\w+-[\w-]+)", text))
    assert len(identifiers) > 20
    for step in ("build_r0", "maintain_r1"):
        for path in (TASK / "steps" / step / "solution" / "workspace").rglob("*"):
            if path.is_file() and path.suffix in {".md", ".py", ".json", ".txt"}:
                content = path.read_text()
                leaked = sorted(i for i in identifiers if i in content)
                assert not leaked, f"{path}: {leaked}"


def test_duplicate_lines_name_the_copy(oracle):
    _, _, call = oracle
    result = json.loads(call("invoice_lines", {"invoice_id": "PRACTICE-R1-DUP-60"}).content)
    [group] = result["confirmed_duplicates"]
    assert group["copy_line_ids"] == ["PRACTICE-R1-DUP-60/L2"]
