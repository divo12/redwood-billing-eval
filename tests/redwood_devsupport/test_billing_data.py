"""Data facts about the read-only billing tables in every seed.

The console never writes these tables, so their invariants are checked here once
instead of on every console write.
"""

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from redwood_kit.billing.store import practice_seed  # noqa: E402
from run import run_agent  # noqa: E402

type State = dict[str, dict[str, dict]]
type Rule = Callable[[State], Iterator[str]]

R1_PRACTICE_SEED = TASK / "environment/r1/development_seed.json"
RELEASE_TODAY = {"R0": "2026-09-15", "R1": "2027-04-08"}
SEEDS: dict[str, Callable[[], State]] = {
    "graded R0": lambda: run_agent.load_db("R0"),
    "graded R1": lambda: run_agent.load_db("R1"),
    "practice R0": lambda: practice_seed("R0"),
    "practice R1": lambda: practice_seed("R1", R1_PRACTICE_SEED),
}


def _created(state: State, account_id: str) -> str:
    return state["accounts"][account_id]["created_on"]


def contracts(state: State) -> Iterator[str]:
    for key, row in state["contracts"].items():
        end = row["end_on"]
        if row["start_on"] < _created(state, row["account_id"]):
            yield f"contracts.{key}: starts before the account exists"
        if end is not None and end < row["start_on"]:
            yield f"contracts.{key}: ends before it starts"


def invoices(state: State) -> Iterator[str]:
    totals = dict.fromkeys(state["invoices"], 0)
    for line in state["invoice_lines"].values():
        totals[line["invoice_id"]] += line["amount_cents"]
    for key, row in state["invoices"].items():
        if row["period_start"] < _created(state, row["account_id"]):
            yield f"invoices.{key}: period precedes account creation"
        if not row["period_start"] <= row["period_end"] <= row["issued_on"]:
            yield f"invoices.{key}: dates out of order"
        if row["total_cents"] != totals[key]:
            yield f"invoices.{key}: total_cents must equal the sum of line amounts"


def invoice_lines(state: State) -> Iterator[str]:
    for key, row in state["invoice_lines"].items():
        invoice = state["invoices"][row["invoice_id"]]
        if not invoice["period_start"] <= row["period_start"] <= row["period_end"] <= invoice["period_end"]:
            yield f"invoice_lines.{key}: period falls outside its invoice period"
        if row["amount_cents"] <= 0:
            yield f"invoice_lines.{key}: amount must be positive"
        for request_id in row["request_ids"]:
            if state["usage"][request_id]["account_id"] != invoice["account_id"]:
                yield f"invoice_lines.{key}: request {request_id} belongs to another account"


def usage(state: State) -> Iterator[str]:
    for key, row in state["usage"].items():
        day = row["at"][:10]
        if min(row["billed_tokens"], row["cached_tokens"]) < 0 or not 100 <= row["http_status"] <= 599:
            yield f"usage.{key}: bad token counts or HTTP status"
        if day < _created(state, row["account_id"]):
            yield f"usage.{key}: precedes account creation"
        yield from _usage_line(state, key, row, day)


def _usage_line(state: State, key: str, row: dict, day: str) -> Iterator[str]:
    if row["line_id"] is None:
        return
    line = state["invoice_lines"][row["line_id"]]
    if state["invoices"][line["invoice_id"]]["account_id"] != row["account_id"]:
        yield f"usage.{key}: line invoice belongs to another account"
    if not line["period_start"] <= day <= line["period_end"]:
        yield f"usage.{key}: falls outside its line period"


def credit_grants(state: State) -> Iterator[str]:
    for key, row in state["credit_grants"].items():
        expires = row["expires_on"]
        if not 0 <= row["remaining_cents"] <= row["amount_cents"]:
            yield f"credit_grants.{key}: remaining must be between zero and amount"
        if row["granted_on"] < _created(state, row["account_id"]):
            yield f"credit_grants.{key}: granted before the account exists"
        if expires is not None and expires < row["granted_on"]:
            yield f"credit_grants.{key}: expires before it is granted"


def credit_ledger(state: State) -> Iterator[str]:
    balances: dict[str, int] = {}
    for key, row in sorted(state["credit_ledger"].items(), key=lambda item: (item[1]["on"], item[0])):
        if (row["amount_cents"] > 0) != (row["entry"] in {"granted", "reinstated"}):
            yield f"credit_ledger.{key}: sign must match the entry kind"
        balances[row["grant_id"]] = balances.get(row["grant_id"], 0) + row["amount_cents"]
        if balances[row["grant_id"]] < 0:
            yield f"credit_ledger.{key}: drops {row['grant_id']} below zero"


RULES: tuple[Rule, ...] = (contracts, invoices, invoice_lines, usage, credit_grants, credit_ledger)


def problems(state: State) -> list[str]:
    return [problem for rule in RULES for problem in rule(state)]


def _dated(state: State) -> Iterator[tuple[str, str]]:
    columns = {
        "accounts": "created_on",
        "contracts": "start_on",
        "invoices": "issued_on",
        "credit_grants": "granted_on",
        "credit_ledger": "on",
    }
    for table, column in columns.items():
        for key, row in state[table].items():
            yield f"{table}.{key}", row[column]
    for key, row in state["usage"].items():
        yield f"usage.{key}", row["at"][:10]


@pytest.mark.parametrize("seed", SEEDS)
def test_read_only_tables_hold_their_invariants(seed):
    assert problems(SEEDS[seed]()) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_seed_holds_nothing_dated_after_its_release(seed):
    today = RELEASE_TODAY[seed.split()[-1]]
    assert [key for key, day in _dated(SEEDS[seed]()) if day > today] == []


@pytest.mark.parametrize("seed", ["graded R0", "practice R0"])
def test_r0_seeds_have_no_credit_ledger(seed):
    assert SEEDS[seed]()["credit_ledger"] == {}


def test_practice_r1_delta_adds_only_new_rows():
    r0, r1 = practice_seed("R0"), practice_seed("R1", R1_PRACTICE_SEED)
    for table, rows in r0.items():
        assert r1[table].items() >= rows.items(), table
    assert set(r1["accounts"]) - set(r0["accounts"]) == {
        "practice-ironbark",
        "practice-juniper",
        "practice-kestrel",
        "practice-larchmont",
    }


def test_invariants_catch_corrupt_rows():
    state = run_agent.load_db("R1")
    invoice = next(iter(state["invoices"].values()))
    invoice["total_cents"] += 1
    grant = next(iter(state["credit_grants"].values()))
    grant["expires_on"] = "2020-01-01"
    line = next(row for row in state["usage"].values() if row["line_id"] is not None)
    line["at"] = "2020-01-01T00:00:00Z"
    state["credit_ledger"]["LEDGER-birchline-apps-2"]["amount_cents"] = 9000
    found = "\n".join(problems(state))
    for expected in ("must equal the sum", "expires before", "outside its line period", "sign must match"):
        assert expected in found


def test_invariants_catch_overdrawn_grant():
    state = run_agent.load_db("R1")
    entry = state["credit_ledger"]["LEDGER-birchline-apps-2"]
    entry["amount_cents"] = -1_000_000
    assert any("below zero" in problem for problem in problems(state))
