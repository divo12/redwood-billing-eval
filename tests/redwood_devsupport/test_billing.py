"""Deterministic tests for the typed billing record store and grader."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import stat
import sys
import threading
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

import advance
from grade import grade
from grade.grade import CheckResult
from grade.scenarios import Release, Scenario
from redwood_kit.billing import Billing as BillingClient
from redwood_kit.billing import BillingError as ClientBillingError
from redwood_kit.billing import development_billing
from redwood_kit.billing import store as billing_store
from redwood_kit.hooks import ToolRunner
from redwood_kit.messages import AssistantMessage, Message, ToolCall, from_dict
from run import gateway, run_agent
from run.run_agent import BillingRecord, ConsoleCall, Result, Termination
from test_run_agent import ScriptedCustomer

BillingError = billing_store.BillingError
R1_OVERLAY = TASK / "environment/r1"
R0_FRAMEWORK = TASK / "environment/app/framework"


def local_console(release):
    return billing_store.local_console(release, R1_OVERLAY / "development_seed.json")


def practice_seed(release):
    return billing_store.practice_seed(release, R1_OVERLAY / "development_seed.json")


def practice_cases(release):
    framework = R1_OVERLAY / "framework" if release == "R1" else R0_FRAMEWORK
    return json.loads((framework / "client_api/practice_cases.json").read_text())
SCENARIOS = json.loads((TASK / "tests" / "data" / "billing_scenarios.json").read_text())["scenarios"]
CASES = {case["id"]: case for case in SCENARIOS}
RELEASES = {"build_r0": "R0", "maintain_r1": "R1"}
COPPERLEAF = "copperleaf-ai"
INV_COPPERLEAF = "INV-2026-0831-207"
LINE_COPPERLEAF_L2 = f"{INV_COPPERLEAF}/L2"
LINE_COPPERLEAF_L3 = f"{INV_COPPERLEAF}/L3"


def query(table: str, where: dict | None = None, cursor: str | None = None) -> dict:
    return {"op": "query", "table": table, "where": where, "cursor": cursor}


def insert(table: str, row: dict) -> dict:
    return {"op": "insert", "table": table, "row": row}


def update(table: str, row_id: str, changes: dict) -> dict:
    return {"op": "update", "table": table, "row_id": row_id, "changes": changes}


def credit(
    account_id: str = COPPERLEAF,
    invoice_id: str = INV_COPPERLEAF,
    line_id: str = LINE_COPPERLEAF_L3,
    amount_cents: int = 34000,
    memo: str = "",
) -> dict:
    return insert("credit_notes", {
        "account_id": account_id,
        "invoice_id": invoice_id,
        "line_ids": [line_id],
        "amount_cents": amount_cents,
        "reason": "duplicate",
        "memo": memo,
    })


def dispute(
    account_id: str,
    invoice_id: str,
    line_id: str,
    request_ids: list[str],
    reason: str | None = "duplicate_charge",
    status: str = "submitted",
) -> dict:
    return insert("disputes", {
        "account_id": account_id,
        "invoice_id": invoice_id,
        "line_ids": [line_id],
        "reason": reason,
        "request_ids": request_ids,
        "window": "the invoice period",
        "status": status,
    })


def case_row(
    account_id: str = COPPERLEAF,
    team: str | None = "billing_ops",
    status: str = "escalated",
    summary: str = "Billing review",
    details: dict[str, str] | None = None,
    priority: str | None = None,
    dispute_id: str | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "team": team,
        "summary": summary,
        "details": {} if details is None else details,
        "dispute_id": dispute_id,
        "status": status,
        **({"priority": priority} if priority else {}),
    }


def record(*requests: dict, release: str = "R0", seed: dict | None = None) -> dict:
    billing = billing_store.Billing(run_agent.load_db(release) if seed is None else seed, release=release)
    for at, request in enumerate(requests, 1):
        try:
            billing.handle(request, at)
        except BillingError:
            pass
    return {"state": billing.state, "log": billing.log}


def as_billing(actual: dict | BillingRecord) -> BillingRecord:
    if isinstance(actual, BillingRecord):
        return actual
    return BillingRecord(state=actual["state"], log=tuple(ConsoleCall.model_validate(e) for e in actual["log"]))


def billing_rows(spec: dict, actual: dict | BillingRecord, release: str = "R0",
                 messages: tuple[Message, ...] = ()) -> dict[str, CheckResult]:
    case = Scenario.model_validate({"id": "inline", "db": spec["db"], "actions": spec.get("actions", [])})
    result = Result(case_id="inline", billing=as_billing(actual))
    return {row.kind.value: row for row in grade.billing_checks(case, result, list(messages), Release(release))}


def checked(case_id: str, result: dict) -> dict[str, CheckResult]:
    case = CASES[case_id]
    return billing_rows(case, result, RELEASES[case["step"]])


CREDIT_CASE = {
    "id": "inline-credit",
    "step": "build_r0",
    "db": {
        "gold": [{
            "op": "insert",
            "table": "credit_notes",
            "row": {
                "account_id": COPPERLEAF,
                "invoice_id": INV_COPPERLEAF,
                "line_ids": [LINE_COPPERLEAF_L2],
                "amount_cents": 34000,
                "reason": "duplicate",
            },
        }],
        "alternatives": [[{
            "op": "insert",
            "table": "credit_notes",
            "row": {
                "account_id": COPPERLEAF,
                "invoice_id": INV_COPPERLEAF,
                "line_ids": [LINE_COPPERLEAF_L3],
                "amount_cents": 34000,
                "reason": "duplicate",
            },
        }]],
    },
    "actions": [{"op": "query:accounts", "equals": {"account_id": COPPERLEAF}}, {"op": "insert:credit_notes"}],
}


def dispute_case(account_id: str, invoice_id: str, line_id: str, request_ids: list[str],
                 alt_line_id: str, case_rows: list[dict]) -> dict:
    return {
        "id": "inline-dispute",
        "step": "build_r0",
        "db": {
            "gold": [
                {
                    "op": "insert",
                    "table": "disputes",
                    "row": {
                        "account_id": account_id,
                        "invoice_id": invoice_id,
                        "line_ids": [line_id],
                        "reason": "duplicate_charge",
                        "request_ids": request_ids,
                        "status": "submitted",
                    },
                },
                *case_rows,
            ],
            "alternatives": [[
                {
                    "op": "insert",
                    "table": "disputes",
                    "row": {
                        "account_id": account_id,
                        "invoice_id": invoice_id,
                        "line_ids": [alt_line_id],
                        "reason": "duplicate_charge",
                        "request_ids": request_ids,
                        "status": "submitted",
                    },
                },
                *case_rows,
            ]],
        },
        "actions": [{"op": "query:accounts", "equals": {"account_id": account_id}}],
    }


def escalated_case_action(account_id: str, team: str, priority: str | None = None) -> dict:
    return {
        "op": "insert",
        "table": "cases",
        "row": {
            "account_id": account_id,
            "team": team,
            "summary": "handoff",
            "status": "escalated",
            "dispute_id": "$0",
            **({"priority": priority} if priority else {}),
        },
    }


def test_shared_database_validates_and_rejects_corrupt_records():
    source = TASK / "data" / "db.json"
    state = json.loads(source.read_text())
    billing_store.validate(state, source)

    bad = copy.deepcopy(state)
    contact = next(iter(bad["contacts"].values()))
    contact["account_id"] = "missing-account"
    with pytest.raises(ValueError, match="unknown accounts id"):
        billing_store.validate(bad, source)

    bad = copy.deepcopy(state)
    next(iter(bad["accounts"].values()))["plan"] = "unlisted"
    with pytest.raises(ValueError, match="must be one of free, self_serve, enterprise"):
        billing_store.validate(bad, source)

    bad = copy.deepcopy(state)
    grant = next(iter(bad["credit_grants"].values()))
    grant["kind"] = "unlisted"
    with pytest.raises(ValueError, match="must be one of promotional, service, prepaid"):
        billing_store.validate(bad, source)



def test_database_validation_rejects_unknown_line_requests():
    state = run_agent.load_db("R1")
    line = next(iter(state["invoice_lines"].values()))
    line["request_ids"].append("missing-request")
    with pytest.raises(ValueError, match="unknown usage id"):
        billing_store.validate(state)


def test_load_db_returns_independent_copies():
    first = run_agent.load_db()
    second = run_agent.load_db()
    first["accounts"].clear()
    assert second["accounts"]
    assert run_agent.load_db()["accounts"]


def test_describe_uses_structure_only_notes_and_r1_money_fields():
    billing = billing_store.Billing()
    described = billing.handle({"op": "describe"}, 1)
    assert set(described) == set(billing_store.TABLES) - {"credit_ledger"}
    assert {table: details["selectors"] for table, details in described.items()} == {
        "accounts": ["account_id", "org"],
        "contacts": ["contact_id", "account_id", "email"],
        "contracts": ["contract_id", "account_id"],
        "invoices": ["invoice_id", "account_id"],
        "invoice_lines": ["line_id", "invoice_id"],
        "usage": ["request_id", "account_id", "line_id"],
        "credit_grants": ["grant_id", "account_id"],
        "credit_notes": ["credit_note_id", "account_id", "invoice_id"],
        "disputes": ["dispute_id", "account_id"],
        "cases": ["case_id", "account_id"],
    }
    assert described["accounts"]["writable"] is False
    assert described["credit_notes"]["writable"] is True
    assert described["cases"]["fields"]["case_id"]["type"] == "string"
    assert described["accounts"]["fields"]["plan_name"]["type"] == "string"
    assert described["credit_grants"]["fields"]["kind"]["enum"] == ["promotional", "service", "prepaid"]
    assert described["cases"]["fields"]["account_id"]["required"] is True
    assert described["cases"]["fields"]["summary"]["required"] is True
    assert described["cases"]["fields"]["details"]["required"] is False
    assert described["cases"]["fields"]["details"]["default"] == {}
    assert described["disputes"]["fields"]["request_ids"]["default"] == []
    assert described["credit_notes"]["fields"]["memo"]["default"] == ""
    assert described["credit_notes"]["fields"]["status"]["default"] == "issued"
    for table in described.values():
        notes = table["notes"].lower()
        assert not any(token in notes for token in ("$", "500", "250", "must", "should"))

    r1 = billing_store.Billing(release="R1").handle({"op": "describe"}, 1)
    assert "total_cents" not in r1["invoices"]["fields"]
    assert r1["invoices"]["fields"]["total"]["type"] == "money"
    assert r1["credit_grants"]["fields"]["amount"]["type"] == "money"
    assert r1["credit_grants"]["fields"]["remaining"]["type"] == "money"


def test_query_equality_contains_paging_and_unknown_fields():
    billing = billing_store.Billing(run_agent.load_db())
    account = billing.state["accounts"][COPPERLEAF]
    result = billing.handle(query("accounts", {"account_id": COPPERLEAF}), 1)
    assert result["rows"] == [account]
    line = next(row for row in billing.state["invoice_lines"].values() if row["request_ids"])
    request_id = line["request_ids"][0]
    assert billing.handle(
        query("invoice_lines", {"invoice_id": line["invoice_id"], "request_ids": request_id}), 2
    )["rows"] == [line]
    assert billing.handle(
        query("invoice_lines", {"line_id": line["line_id"], "request_ids": line["request_ids"]}), 3
    )["rows"] == [line]

    usage_counts: dict[str, int] = {}
    for row in billing.state["usage"].values():
        usage_counts[row["account_id"]] = usage_counts.get(row["account_id"], 0) + 1
    account_id = next(
        account_id for account_id, count in usage_counts.items() if count > billing_store.PAGE_SIZE
    )
    account_usage = sorted(
        row["request_id"] for row in billing.state["usage"].values()
        if row["account_id"] == account_id
    )
    first = billing.handle(query("usage", {"account_id": account_id}), 4)
    assert [row["request_id"] for row in first["rows"]] == account_usage[:billing_store.PAGE_SIZE]
    assert first["next_cursor"]
    second = billing.handle(
        query("usage", {"account_id": account_id}, first["next_cursor"]), 5
    )
    assert [row["request_id"] for row in second["rows"]] == account_usage[
        billing_store.PAGE_SIZE:2 * billing_store.PAGE_SIZE
    ]
    with pytest.raises(BillingError, match="unknown table"):
        billing.handle(query("missing"), 6)
    with pytest.raises(BillingError, match="unknown field"):
        billing.handle(query("accounts", {"account_id": COPPERLEAF, "missing": "x"}), 7)


def test_unfiltered_and_nonselector_queries_are_rejected_and_logged():
    billing = billing_store.Billing()
    for where in (None, {"status": "active"}):
        with pytest.raises(BillingError, match="query on accounts needs one of: account_id, org"):
            billing.handle(query("accounts", where), len(billing.log) + 1)
        assert billing.log[-1].ok is False
        assert billing.log[-1].error == "query on accounts needs one of: account_id, org"


def test_insert_and_update_enforce_integrity_and_rejections_change_nothing():
    billing = billing_store.Billing(run_agent.load_db())
    original = copy.deepcopy(billing.state)
    bad_line = credit(line_id="missing-line")
    with pytest.raises(BillingError, match="unknown invoice_lines id"):
        billing.handle(bad_line, 1)
    assert billing.state == original and billing.log[-1].ok is False

    wrong_invoice_line = next(
        line for line in billing.state["invoice_lines"].values()
        if line["invoice_id"] != INV_COPPERLEAF
    )
    with pytest.raises(BillingError, match="lines must belong to the invoice"):
        billing.handle(credit(line_id=wrong_invoice_line["line_id"]), 2)
    assert billing.state == original

    with pytest.raises(BillingError, match="unknown request id missing-request for this account"):
        billing.handle(
            dispute(COPPERLEAF, INV_COPPERLEAF, LINE_COPPERLEAF_L3, ["missing-request"]), 3
        )
    assert billing.state == original

    with pytest.raises(BillingError, match="read-only"):
        billing.handle(insert("accounts", {"account_id": "x"}), 4)
    with pytest.raises(BillingError, match="server-assigned"):
        billing.handle(insert("cases", case_row() | {"case_id": "CASE-9"}), 5)

    draft = billing.handle(insert("cases", case_row(team=None, status="draft", summary="")), 6)
    state = copy.deepcopy(billing.state)
    with pytest.raises(BillingError, match="unknown disputes id"):
        billing.handle(update("cases", draft["case_id"], {"dispute_id": "DSP-999"}), 7)
    assert billing.state == state


def test_dispute_request_ids_must_belong_to_the_same_account():
    state = run_agent.load_db()
    other = next(account for account in state["accounts"] if account != COPPERLEAF)
    request_id = next(row["request_id"] for row in state["usage"].values()
                      if row["account_id"] == other)
    billing = billing_store.Billing(state)
    with pytest.raises(BillingError, match=f"unknown request id {request_id} for this account"):
        billing.handle(
            dispute(COPPERLEAF, INV_COPPERLEAF, LINE_COPPERLEAF_L3, [request_id]), 1
        )
    assert billing.log[-1].ok is False


def test_lifecycles_direct_final_status_seed_immutability_and_sparse_ids():
    billing = billing_store.Billing(run_agent.load_db())
    minimal_case = billing.handle(insert("cases", {
        "account_id": COPPERLEAF,
        "summary": "Draft case",
        "status": "draft",
    }), 1)
    assert minimal_case["team"] is None
    assert minimal_case["details"] == {}
    assert "priority" not in minimal_case
    assert minimal_case["dispute_id"] is None
    with pytest.raises(BillingError, match="escalated cases need a team"):
        billing.handle(insert("cases", case_row(team=None)), 2)
    assert billing.log[-1].ok is False
    submitted = dispute(
        "tidewell-labs",
        "INV-2026-0831-114",
        "INV-2026-0831-114/L3",
        ["req_a91", "req_a92"],
    )
    receipt = billing.handle(submitted, 3)
    assert receipt["status"] == "submitted"
    with pytest.raises(BillingError, match="not draft"):
        billing.handle(update("disputes", receipt["dispute_id"], {"window": "changed"}), 4)

    draft = billing.handle(insert("cases", case_row(team=None, status="draft", summary="")), 5)
    escalated = billing.handle(
        update("cases", draft["case_id"], {"team": "billing_ops", "summary": "Escalate", "status": "escalated"}),
        6,
    )
    assert escalated["status"] == "escalated"

    seed = run_agent.load_db()
    seed["cases"]["CASE-2"] = {
        "case_id": "CASE-2",
        "account_id": COPPERLEAF,
        "team": None,
        "summary": "Seeded draft",
        "details": {},
        "priority": None,
        "dispute_id": None,
        "status": "draft",
        "at": 0,
    }
    sparse = billing_store.Billing(seed)
    case = sparse.handle(insert("cases", case_row(team=None, status="draft")), 1)
    assert case["case_id"] == "CASE-1"

    seed["credit_notes"] = {
        "CN-2": {
            "credit_note_id": "CN-2",
            "account_id": COPPERLEAF,
            "invoice_id": INV_COPPERLEAF,
            "line_ids": [f"{INV_COPPERLEAF}/L1"],
            "amount_cents": 1,
            "reason": "goodwill",
            "memo": "history",
            "status": "issued",
            "at": 0,
        }
    }
    sparse_credit = billing_store.Billing(seed)
    created = sparse_credit.handle(credit(line_id=LINE_COPPERLEAF_L3, amount_cents=1), 1)
    assert created["credit_note_id"] == "CN-1"

    seed["disputes"]["DSP-2"] = {
        "dispute_id": "DSP-2",
        "account_id": "tidewell-labs",
        "invoice_id": "INV-2026-0831-114",
        "line_ids": ["INV-2026-0831-114/L3"],
        "reason": None,
        "request_ids": [],
        "window": "",
        "status": "draft",
        "at": 0,
    }
    sparse_dispute = billing_store.Billing(seed)
    created_dispute = sparse_dispute.handle(
        dispute("tidewell-labs", "INV-2026-0831-114", "INV-2026-0831-114/L3",
                ["req_a91", "req_a92"], status="draft"),
        1,
    )
    assert created_dispute["dispute_id"] == "DSP-1"

    historical = run_agent.load_db()
    historical["cases"]["CASE-2"] = copy.deepcopy(seed["cases"]["CASE-2"])
    history = billing_store.Billing(historical)
    with pytest.raises(BillingError, match="seed history rows are immutable"):
        history.handle(update("cases", "CASE-2", {"summary": "changed"}), 1)


def test_submitted_dispute_requires_request_ids_and_r1_case_requires_priority():
    billing = billing_store.Billing(run_agent.load_db())
    missing_requests = dispute(
        "tidewell-labs",
        "INV-2026-0831-114",
        "INV-2026-0831-114/L3",
        [],
        status="submitted",
    )
    missing_requests["row"].pop("request_ids")
    with pytest.raises(BillingError, match="request_ids"):
        billing.handle(missing_requests, 1)
    assert billing.log[-1].ok is False

    r1 = billing_store.Billing(run_agent.load_db("R1"), release="R1")
    with pytest.raises(BillingError, match="priority is required: one of normal, urgent"):
        r1.handle(insert("cases", {
            "account_id": COPPERLEAF,
            "team": "billing_ops",
            "summary": "Escalate",
            "status": "escalated",
        }), 1)
    assert r1.log[-1].ok is False


def test_escalated_case_finalizes_console_and_logs_later_rejection():
    billing = billing_store.Billing(run_agent.load_db())
    billing.handle(query("accounts", {"account_id": COPPERLEAF}), 1)
    receipt = billing.handle(insert("cases", case_row()), 2)
    assert receipt["status"] == "escalated"
    assert receipt["at"] == 2
    state = copy.deepcopy(billing.state)
    with pytest.raises(BillingError, match="already handed to billing_ops as CASE-1; the console is closed"):
        billing.handle(query("accounts"), 3)
    assert billing.state == state
    assert billing.log[-1].ok is False
    assert "already handed to billing_ops" in billing.log[-1].error


def test_credit_ledger_is_an_r1_only_read_only_table():
    seed = run_agent.load_db("R1")
    r0 = billing_store.Billing(run_agent.load_db("R0"), release="R0")
    assert "credit_ledger" not in r0.describe()
    assert r0.state["credit_ledger"] == {}
    with pytest.raises(BillingError, match="unknown table 'credit_ledger'"):
        r0.handle(
            query("credit_ledger", {"account_id": "birchline-apps"}), 1
        )
    r1 = billing_store.Billing(seed, release="R1")
    result = r1.handle(query("credit_ledger", {"account_id": "birchline-apps"}), 1)
    assert sum(row["amount_cents"] for row in r1.state["credit_ledger"].values()
               if row["account_id"] == "birchline-apps") == 6000
    applied = next(row for row in result["rows"] if row["entry"] == "applied")
    assert applied["amount"] == {"value": "-90.00", "currency": "USD"}
    assert applied["invoice_id"] == "INV-BG-202703-039"
    with pytest.raises(BillingError, match="credit_ledger is read-only"):
        r1.handle(insert("credit_ledger", {"entry_id": "X"}), 2)


def test_march_2027_invoices_and_usage_validate():
    seed = run_agent.load_db("R1")
    for invoice_id, account_id, total in (
        ("INV-2027-0331-340", "kestrel-apps", 420000),
        ("INV-2027-0331-350", "northwind-ai", 264000),
        ("INV-2027-0331-360", "saffron-labs", 155000),
    ):
        invoice = seed["invoices"][invoice_id]
        assert (invoice["account_id"], invoice["status"], invoice["total_cents"]) == (
            account_id, "open", total)
        lines = [row for row in seed["invoice_lines"].values() if row["invoice_id"] == invoice_id]
        assert sum(row["amount_cents"] for row in lines) == total


def test_r1_delta_merge_rejects_duplicate_ids(tmp_path, monkeypatch):
    duplicate = {table: {} for table in billing_store.TABLES}
    duplicate["accounts"]["copperleaf-ai"] = run_agent.load_db("R0")["accounts"]["copperleaf-ai"]
    delta_path = tmp_path / "db_r1.json"
    delta_path.write_text(json.dumps(duplicate))
    monkeypatch.setattr(run_agent, "DB_R1_PATHS", (delta_path,))
    with pytest.raises(ValueError, match="already exists"):
        run_agent.load_db("R1")


def test_r0_seed_lacks_2027_invoice_that_r1_adds():
    invoice_id = "INV-2027-0331-240"
    r0 = billing_store.Billing(run_agent.load_db("R0"), release="R0")
    r1 = billing_store.Billing(run_agent.load_db("R1"), release="R1")
    assert r0.handle(query("invoices", {"invoice_id": invoice_id}), 1)["rows"] == []
    assert r1.handle(query("invoices", {"invoice_id": invoice_id}), 1)["rows"]
    with pytest.raises(BillingError, match="unknown invoices id"):
        r0.handle(insert("credit_notes", {
            "account_id": "bluefin-labs",
            "invoice_id": invoice_id,
            "line_ids": [f"{invoice_id}/L1"],
            "amount_cents": 1,
            "reason": "duplicate",
            "memo": "",
        }), 2)
    assert r0.log[-1].ok is False
    future_credit = {
        "account_id": "bluefin-labs",
        "invoice_id": invoice_id,
        "line_ids": [f"{invoice_id}/L1"],
        "amount": {"value": "0.01", "currency": "USD"},
        "reason": "duplicate",
        "memo": "",
    }
    assert r1.handle(insert("credit_notes", future_credit), 2)["status"] == "issued"
    assert COPPERLEAF in r0.state["accounts"]
    assert "marrowline-analytics" not in r0.state["accounts"]


def test_r1_money_reads_writes_and_escalation_priority():
    billing = billing_store.Billing(run_agent.load_db("R1"), release="R1")
    invoice = billing.handle(query("invoices", {"invoice_id": INV_COPPERLEAF}), 1)["rows"][0]
    assert invoice["total"] == {"value": "2800.00", "currency": "USD"}
    assert "total_cents" not in invoice
    line = billing.handle(query("invoice_lines", {"line_id": LINE_COPPERLEAF_L3}), 2)["rows"][0]
    assert line["amount"] == {"value": "340.00", "currency": "USD"}
    grants = billing.handle(query("credit_grants", {"account_id": COPPERLEAF}), 3)["rows"]
    if grants:
        assert all("amount" in grant and "remaining" in grant for grant in grants)

    row = credit(line_id=LINE_COPPERLEAF_L3, amount_cents=34000)["row"]
    row.pop("amount_cents")
    row["amount"] = {"value": "340.00", "currency": "USD"}
    note = billing.handle(insert("credit_notes", row), 4)
    assert note["amount"] == {"value": "340.00", "currency": "USD"}
    assert billing.state["credit_notes"][note["credit_note_id"]]["amount_cents"] == 34000
    state = copy.deepcopy(billing.state)
    with pytest.raises(BillingError, match="credit_notes are insert-only"):
        billing.handle(update("credit_notes", note["credit_note_id"], {"memo": "changed"}), 5)
    assert billing.state == state
    assert billing.handle(
        query("credit_notes", {"credit_note_id": note["credit_note_id"]}), 6
    )["rows"][0]["amount"] == note["amount"]

    with pytest.raises(BillingError, match="unknown field 'amount_cents'"):
        billing.handle(credit(line_id=LINE_COPPERLEAF_L3, amount_cents=100), 7)
    invalid_money = copy.deepcopy(row)
    invalid_money["amount"] = {"value": "340.0", "currency": "USD"}
    with pytest.raises(BillingError, match="two-decimal USD value"):
        billing.handle(insert("credit_notes", invalid_money), 8)

    case = case_row(priority=None)
    with pytest.raises(BillingError, match="priority is required: one of normal, urgent"):
        billing.handle(insert("cases", case), 9)
    case["priority"] = "normal"
    escalated = billing.handle(insert("cases", case), 10)
    assert escalated["priority"] == "normal"
    assert billing.state["cases"][escalated["case_id"]]["priority"] == "normal"


def test_replay_resolves_nested_action_references_and_projects_all_writable_tables():
    actions = [
        {
            "op": "insert",
            "table": "credit_notes",
            "row": credit(line_id=LINE_COPPERLEAF_L3, amount_cents=1)["row"],
        },
        {
            "op": "insert",
            "table": "disputes",
            "row": {
                "account_id": "tidewell-labs",
                "invoice_id": "INV-2026-0831-114",
                "line_ids": ["INV-2026-0831-114/L3"],
                "reason": "duplicate_charge",
                "request_ids": ["req_a91", "req_a92"],
                "window": "August",
                "status": "draft",
            },
        },
        {
            "op": "update",
            "table": "disputes",
            "row_id": "$1",
            "changes": {"status": "submitted"},
        },
        {
            "op": "insert",
            "table": "cases",
            "row": case_row(
                account_id="tidewell-labs",
                team="billing_ops",
                dispute_id="$1",
                priority="normal",
            ),
        },
    ]
    state = billing_store.replay(run_agent.load_db("R1"), actions, release="R1")
    assert state["disputes"]["DSP-1"]["status"] == "submitted"
    assert state["cases"]["CASE-1"]["dispute_id"] == "DSP-1"
    projection = billing_store.project(state)
    assert set(projection) == {"credit_notes", "disputes", "cases"}
    assert projection["credit_notes"][0]["amount_cents"] == 1
    assert projection["disputes"][0]["reason"] == "duplicate_charge"
    assert projection["cases"][0] == {
        "account_id": "tidewell-labs",
        "team": "billing_ops",
        "status": "escalated",
        "dispute_id": projection["disputes"][0],
    }


def test_projection_drops_draft_disputes_and_cases():
    billing = billing_store.Billing(run_agent.load_db())
    billing.handle(insert("disputes", {
        "account_id": COPPERLEAF,
        "invoice_id": None,
        "line_ids": [],
        "reason": None,
        "request_ids": [],
        "window": "",
        "status": "draft",
    }), 1)
    billing.handle(insert("cases", case_row(team=None, status="draft")), 2)
    assert billing_store.project(billing.state) == {
        "credit_notes": [],
        "disputes": [],
        "cases": [],
    }


def test_excludes_holds_on_every_matching_write_and_the_lookup_is_its_own_action():
    account_id = "tidewell-labs"
    spec = {"db": {"gold": []}, "actions": [
        {"op": "query:accounts", "equals": {"account_id": account_id}},
        {"op": "write:cases", "excludes": ["4111111111111111"]},
    ]}

    dirty = record(
        query("accounts", {"account_id": account_id}),
        insert("cases", case_row(account_id=account_id, status="draft",
                                 summary="ref 4111 1111 1111 1111")),
        update("cases", "CASE-1", {"summary": "redacted"}),
    )
    assert not billing_rows(spec, dirty)["action"].passed  # the dirty insert fails even though the update is clean

    clean = record(
        query("accounts", {"account_id": account_id}),
        insert("cases", case_row(account_id=account_id, status="draft", summary="redacted")),
        update("cases", "CASE-1", {"summary": "still redacted"}),
    )
    assert billing_rows(spec, clean)["action"].passed

    no_write = record(query("accounts", {"account_id": account_id}))
    assert "no successful write:cases()" in billing_rows(spec, no_write)["action"].problem

    without_lookup = record(insert("cases", case_row(account_id=account_id, status="draft", summary="redacted")))
    rows = billing_rows(spec, without_lookup)
    assert "no successful query:accounts(account_id=tidewell-labs)" in rows["action"].problem


def test_every_scenario_replays_in_release_and_matches_empty_trace_semantics():
    assert len(SCENARIOS) == 20
    baseline = run_agent.load_db("R1")
    for step, release in RELEASES.items():
        step_cases = [case for case in SCENARIOS if case["step"] == step]
        for case in step_cases:
            release_state = billing_store.Billing(baseline, release=release).state
            scenario_text = json.dumps(case["scenario"])
            orgs = [account_id for account_id in release_state["accounts"] if account_id in scenario_text]
            assert orgs, case["id"]
            before = billing_store.project(release_state)
            traces = (case["db"]["gold"], *(case["db"].get("alternatives") or []))
            for actions in traces:
                state = billing_store.replay(baseline, actions, release=release)
                assert (billing_store.project(state) != before) == bool(actions), case["id"]
            empty = {"state": release_state, "log": []}
            db_row = checked(case["id"], empty)["db"]
            assert db_row.passed is (not case["db"]["gold"]), case["id"]



def test_r0_traces_also_replay_on_the_r1_console_for_q10():
    baseline = run_agent.load_db("R1")
    for case in (case for case in SCENARIOS if case["step"] == "build_r0"):
        for actions in (case["db"]["gold"], *(case["db"].get("alternatives") or [])):
            billing_store.replay(baseline, actions, release=RELEASES["maintain_r1"])

@pytest.mark.parametrize("case_id", ["a0-12", "a0-24", "a0-25"])
def test_added_case_facts_pass_action_and_billing_checks(case_id):
    case = CASES[case_id]
    release = RELEASES[case["step"]]
    actions = case["db"]["gold"]
    case_action = next(action for action in actions if action.get("table") == "cases")
    billing = billing_store.Billing(run_agent.load_db("R1"), release=release)
    billing.handle(query("accounts", {"account_id": case_action["row"]["account_id"]}), 1)
    action_ids = {}
    for index, action in enumerate(actions):
        request = copy.deepcopy(action)
        if request["op"] == "insert":
            for name, value in request["row"].items():
                if isinstance(value, str) and value.startswith("$"):
                    request["row"][name] = action_ids[int(value[1:])]
        result = billing.handle(request, index + 2)
        if request["op"] == "insert":
            action_ids[index] = result[billing_store.ID_FIELDS[request["table"]]]
    actual = {"state": billing.state, "log": billing.log}
    facts = next(action["contains"] for action in case["actions"] if "contains" in action)
    said = (from_dict({"role": "user", "content": " ".join(facts)}),)
    assert all(row.passed for row in billing_rows(case, actual, release, said).values()), case_id
    assert not billing_rows(case, actual, release)["action"].passed, case_id  # the gold summary alone lacks them


def _load_oracle_classes(release: str, tmp_path: Path):
    source = TASK / "steps" / "build_r0" / "solution"
    workspace = tmp_path / f"oracle-{release}"
    shutil.copytree(
        source / "workspace",
        workspace,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if release == "R1":
        shutil.copy(
            TASK / "steps" / "maintain_r1" / "solution" / "workspace" / "billing_rules.py",
            workspace / "billing_rules.py",
        )

    previous = sys.modules.pop("billing_rules", None)
    sys.path.insert(0, str(workspace))
    try:
        spec = importlib.util.spec_from_file_location(
            f"oracle_tools_{release}", workspace / "tools.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(workspace))
        if previous is None:
            sys.modules.pop("billing_rules", None)
        else:
            sys.modules["billing_rules"] = previous
    hook_spec = importlib.util.spec_from_file_location(
        f"oracle_hooks_{release}", workspace / "hooks.py"
    )
    hook_module = importlib.util.module_from_spec(hook_spec)
    hook_spec.loader.exec_module(hook_module)
    return module.Tools, hook_module.Hooks


@pytest.mark.parametrize("release", ["R0"])
def test_oracle_console_tools_verify_duplicates_and_require_account_lookup(
    release, tmp_path
):
    tools_type, hooks_type = _load_oracle_classes(release, tmp_path)
    tools = tools_type()
    tools.billing = local_console(release)
    hooks = hooks_type()
    runner = ToolRunner(tools, hooks)
    call_number = 0

    def call(name, arguments):
        nonlocal call_number
        call_number += 1
        return runner.run(ToolCall(str(call_number), name, arguments))

    for write_tool in ("credit_duplicate", "open_dispute", "escalate_case"):
        result = call(write_tool, {"account_id": "practice-alderwick"})
        assert result.error and "Blocked:" in result.content

    account = "practice-alderwick"
    invoice = "PRACTICE-DUP-180"
    found = call("find_account", {"account_id": account})
    assert not found.error
    under_limit_dispute = call(
        "open_dispute",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert (
        under_limit_dispute.error
        and "use credit_duplicate" in under_limit_dispute.content
    )
    credited = call(
        "credit_duplicate",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert not credited.error
    credit_result = json.loads(credited.content)
    assert credit_result["amount_credited"] == "$180.00"
    note = credit_result["credit_note"]
    assert note["reason"] == "duplicate"
    if release == "R0":
        assert note["amount_cents"] == 18_000
    else:
        assert note["amount"] == {"value": "180.00", "currency": "USD"}

    account = "practice-copperfern"
    invoice = "PRACTICE-DUP-900"
    assert not call("find_account", {"account_id": account}).error
    over_limit = call(
        "credit_duplicate",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert over_limit.error and "exceeds the console credit limit" in over_limit.content
    duplicate_info = json.loads(
        call("invoice_lines", {"invoice_id": invoice}).content
    )["confirmed_duplicates"][0]
    dispute = call(
        "open_dispute",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert not dispute.error
    dispute_record = json.loads(dispute.content)
    assert dispute_record["reason"] == "duplicate_charge"
    # R0 support-console-billing-credits-and-disputes: "Open a dispute on the duplicated line".
    assert dispute_record["line_ids"] == [f"{invoice}/L1"]
    assert set(dispute_record["line_ids"]) <= set(duplicate_info["line_ids"])
    assert dispute_record["request_ids"] == duplicate_info["request_ids"]

    account = "practice-dawnmere"
    invoice = "PRACTICE-LOOKALIKE"
    assert not call("find_account", {"account_id": account}).error
    lookalike = call(
        "credit_duplicate",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert lookalike.error and "lookalikes, not confirmed duplicates" in lookalike.content
    lookalike_dispute = call(
        "open_dispute",
        {
            "account_id": account,
            "invoice_id": invoice,
            "line_id": f"{invoice}/L1",
        },
    )
    assert (
        lookalike_dispute.error
        and "follow current billing-routing guidance" in lookalike_dispute.content
    )

    routing_rule = (
        "Disputed or duplicated line on a direct Redwood invoice | Billing Ops | "
        "Invoice number, the line(s), UTC window, request IDs"
    )
    hooks.remember_policy(
        "search_policy", {}, {"results": [{"text": routing_rule}]}
    )
    account = "practice-copperfern"
    invoice = "PRACTICE-DUP-900"
    escalation_args = {
        "account_id": account,
        "team": "billing_ops",
        "summary": "Review a disputed invoice line",
        "details": {"invoice_id": invoice},
        "unresolved": "The invoice-line dispute needs Billing Ops review.",
        "routing_rule": routing_rule.replace(" | ", "  |\n "),
    }
    escalation = call("escalate_case", escalation_args)
    assert not escalation.error
    blocked_route = call(
        "escalate_case",
        {**escalation_args, "routing_rule": "Billing Ops should review it."},
    )
    assert blocked_route.error and "Blocked:" in blocked_route.content
    blocked_team = call(
        "escalate_case",
        {**escalation_args, "team": "finance"},
    )
    assert blocked_team.error and "Blocked:" in blocked_team.content
    blocked_unresolved = call(
        "escalate_case",
        {**escalation_args, "unresolved": " "},
    )
    assert blocked_unresolved.error and "Blocked:" in blocked_unresolved.content


def test_build_index_selects_every_provenance_source_without_embedding():
    path = TASK / "steps" / "build_r0" / "solution" / "workspace" / "build_index.py"
    spec = importlib.util.spec_from_file_location("oracle_build_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provenance = json.loads((TASK / "tests" / "data" / "provenance.json").read_text())
    root = TASK / "environment" / "corpus"
    for rule in provenance["rules"].values():
        for release in rule["releases"].values():
            for record in release["records"]:
                if record["role"] in {"contradicting", "distractor"}:
                    continue
                source = record["path"]
                data = json.loads((root / source).read_text())
                assert module.is_relevant(source, data), source


def _dollars(cents):
    return f"{cents / 100:.2f}".removesuffix(".00")


def _lines(state, invoice_id, product=None):
    return [line["amount_cents"] for line in state["invoice_lines"].values()
            if line["invoice_id"] == invoice_id and product in (None, line["product"])]


def _plan(state, account_id):
    return state["accounts"][account_id]["plan_name"]


def _derived(state):
    """Each `communicate` value recomputed from the console rows it names."""
    usage = state["usage"].values()
    return {
        "a0-02": [_dollars(state["invoices"]["INV-BG-202608-002"]["total_cents"]),
                  _dollars(sum(_lines(state, "INV-BG-202608-002", "hosted_api"))),
                  _dollars(sum(_lines(state, "INV-BG-202608-002", "plan_fee")))],
        "a0-04": [str(sum(u["billed_tokens"] for u in usage
                          if u["account_id"] == "lumenfield-ai" and u["at"].startswith("2026-08")))],
        "a0-05": [_dollars(state["invoices"]["INV-2026-0831-150"]["total_cents"])],
        "a0-10": [_dollars(state["invoice_lines"]["INV-BG-202608-009/L1"]["amount_cents"])],
        "a0-24": [_dollars(state["invoices"]["INV-2026-0831-301"]["total_cents"])],
        "a0-25": [_dollars(state["invoice_lines"]["INV-2026-0831-114/L3"]["amount_cents"])],
        "a0-31": [_dollars(state["invoices"]["INV-2026-0831-455"]["total_cents"])],
    }


@pytest.mark.parametrize("release", ["R0", "R1"])
def test_communicate_values_match_the_console_in_both_releases_and_are_not_in_the_brief(release):
    derived = _derived(run_agent.load_db(release))
    for case in SCENARIOS:
        if case["step"] != "build_r0" or not case.get("communicate"):
            continue
        brief = grade._plain(json.dumps(case["scenario"]))
        for forms in case["communicate"]:
            assert not any(grade._says(form, brief) for form in forms), (case["id"], forms)
        if case["id"] in derived:
            assert [forms[0] for forms in case["communicate"]] == derived[case["id"]], case["id"]


def _ledger_balance(state, account_id):
    return sum(entry["amount_cents"] for entry in state["credit_ledger"].values()
               if entry["account_id"] == account_id)


def _derived_r1(state):
    """Each R1 `communicate` value recomputed from the R1 console rows it names."""
    lines = state["invoice_lines"]
    expiry = {entry["grant_id"]: entry["expires_on"] for entry in state["credit_ledger"].values() if entry["expires_on"]}
    return {
        "a1-05": [_dollars(lines["INV-2027-0331-118/L3"]["amount_cents"])],
        "a1-07": [_dollars(lines["INV-2027-0331-310/L3"]["amount_cents"]), lines["INV-2027-0331-310/L3"]["invoice_id"]],
        "a1-08": [_dollars(lines["INV-2027-0331-350/L3"]["amount_cents"])],
        "a1-09": [_dollars(_ledger_balance(state, "echo-valley")),
                  expiry["GRANT-echo-valley-1"], expiry["GRANT-echo-valley-2"]],
        "a1-10": [expiry["GRANT-cinder-analytics-1"], "90 days"],  # the expiry rule is R1 policy, not console data
        "a1-15": [{"aws_marketplace": "AWS Marketplace"}[state["accounts"]["ironbark-cloud"]["billing_channel"]]],
    }


def test_r1_communicate_values_match_the_r1_console_and_are_not_in_the_brief():
    state = billing_store.Billing(run_agent.load_db("R1"), release="R1").state
    derived = _derived_r1(state)
    for case in SCENARIOS:
        if case["step"] != "maintain_r1" or not case.get("communicate"):
            continue
        brief = grade._plain(json.dumps(case["scenario"]))
        for forms in case["communicate"]:
            assert not any(grade._says(form, brief) for form in forms), (case["id"], forms)
        if case["id"] in derived:
            assert [forms[0] for forms in case["communicate"]] == derived[case["id"]], case["id"]


def test_echo_valley_frozen_grants_disagree_with_the_ledger():
    state = billing_store.Billing(run_agent.load_db("R1"), release="R1").state
    frozen = sum(g["remaining_cents"] for g in state["credit_grants"].values()
                 if g["account_id"] == "echo-valley")
    assert (frozen, _ledger_balance(state, "echo-valley")) == (20000, 25000)


def r1_credit(account_id: str, invoice_id: str, line_id: str, dollars: str) -> dict:
    request = credit(account_id, invoice_id, line_id)
    request["row"].pop("amount_cents")
    request["row"]["amount"] = {"value": dollars, "currency": "USD"}
    return request


@pytest.mark.parametrize("case_id, r0_gold", [
    ("a1-05", [r1_credit("marrowline-analytics", "INV-2027-0331-118", "INV-2027-0331-118/L3", "300.00")]),
    ("a1-07", [r1_credit("cobaltbay", "INV-2027-0331-310", "INV-2027-0331-310/L3", "250.00")]),
    ("a1-08", [r1_credit("northwind-ai", "INV-2027-0331-350", "INV-2027-0331-350/L3", "120.00")]),
    ("a1-18", [insert("cases", {"account_id": "tidewell-labs", "team": "billing_ops", "summary": "handoff",
                                "status": "escalated", "priority": "normal"})]),
])
def test_r1_gold_differs_from_what_the_r0_rule_would_write(case_id, r0_gold):
    """Changed-value witness: the write the R0 rule prescribes fails the R1 case's DB check."""
    case = CASES[case_id]
    actual = record(query("accounts", {"account_id": case["actions"][0]["equals"]["account_id"]}),
                    *r0_gold, release="R1")
    assert not billing_rows(case, actual, "R1")["db"].passed, case_id
    assert actual["log"][-1].ok, case_id


def test_every_scenario_rule_cites_records_visible_in_its_release():
    provenance = json.loads((TASK / "tests" / "data" / "provenance.json").read_text())["rules"]
    scenarios = json.loads((TASK / "tests" / "data" / "billing_scenarios.json").read_text())["scenarios"]
    root = TASK / "environment" / "corpus"
    visible = {"R0": ("R0/",), "R1": ("R0/", "R1/")}
    carriers = {"full", "partial", "superseding"}
    needed = {(rule, "R0" if case["step"] == "build_r0" else "R1") for case in scenarios for rule in case["rules"]}
    needed |= {(rule, "R1") for rule, release in list(needed) if release == "R0"}
    for rule, release in sorted(needed):
        assert release in provenance.get(rule, {}).get("releases", {}), (rule, release)
        records = provenance[rule]["releases"][release]["records"]
        assert {record["role"] for record in records} & carriers, (rule, release)
        for record in records:
            assert record["path"].startswith(visible[release]), (rule, release, record["path"])
            assert (root / record["path"]).is_file(), record["path"]


def test_every_r1_scenario_depends_on_a_record_that_exists_only_in_r1():
    provenance = json.loads((TASK / "tests" / "data" / "provenance.json").read_text())["rules"]
    scenarios = json.loads((TASK / "tests" / "data" / "billing_scenarios.json").read_text())["scenarios"]
    carriers = {"full", "partial", "superseding"}

    def r1_only(rule):
        records = provenance[rule]["releases"].get("R1", {}).get("records", [])
        return any(r["role"] in carriers and r["path"].startswith("R1/") for r in records)

    unchanged = [case["id"] for case in scenarios
                 if case["step"] == "maintain_r1" and not any(r1_only(rule) for rule in case["rules"])]
    assert not unchanged, f"R1 scenarios that no R1 record changes: {unchanged}"


def test_build_index_fts_supports_phrase_queries(tmp_path, monkeypatch):
    path = TASK / "steps" / "build_r0" / "solution" / "workspace" / "build_index.py"
    spec = importlib.util.spec_from_file_location("oracle_build_index_fts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "R0"
    source = root / "sources" / "support-process" / "billing-policy.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "title": "Billing duplicate policy",
        "status": "published",
        "body": "Confirm duplicate billing lines in the invoice console.",
    }))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(module, "embed", lambda model, inputs: [[0.0, 1.0] for _ in inputs])
    module.build_index([root], workspace)
    table = module.lancedb.connect(str(workspace / "evidence.lance")).open_table("evidence")
    assert table.search('"duplicate billing"', query_type="fts").limit(1).to_list()


def test_oracle_files_contain_no_graded_billing_identifiers():
    database = json.loads((TASK / "data" / "db.json").read_text())
    fields = {"account_id", "org", "email", "invoice_id", "line_id", "request_id"}
    identifiers = {
        row[field]
        for table in database.values()
        for row in table.values()
        for field in fields
        if isinstance(row.get(field), str) and row[field]
    }
    for rows in database.values():
        for key, row in rows.items():
            if key:
                identifiers.add(key)
            identifiers.update(
                request_id
                for request_id in row.get("request_ids", [])
                if isinstance(request_id, str) and request_id
            )
    solutions = (
        TASK / "steps" / "build_r0" / "solution",
        TASK / "steps" / "maintain_r1" / "solution",
    )
    for solution in solutions:
        for path in solution.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(errors="ignore")
            assert not any(identifier in text for identifier in identifiers), path


def test_oracle_prompt_files_stay_within_size_limits():
    workspace = TASK / "steps" / "build_r0" / "solution" / "workspace"
    assert len((workspace / "system_prompt.md").read_text()) < 1500
    assert len((workspace / "AGENTS.md").read_text()) < 4000


def test_background_data_has_expected_size_and_no_accidental_lookalikes():
    state = run_agent.load_db("R1")
    assert 55 <= len(state["accounts"]) <= 60
    assert 2500 <= len(state["usage"]) <= 3500
    marketplace = {
        account_id for account_id, account in state["accounts"].items()
        if account["billing_channel"] != "direct"
    }
    assert all(invoice["account_id"] not in marketplace for invoice in state["invoices"].values())
    assert all(row["line_id"] is None for row in state["usage"].values()
               if row["account_id"] in marketplace)
    allowed = {
        "tidewell-labs",
        "copperleaf-ai",
        "marrowline-analytics",
        "cobaltbay",
        "saltmarsh-data",
        "kestrel-apps",
        "northwind-ai",
        "saffron-labs",
    }
    duplicates = set()
    for invoice_id, invoice in state["invoices"].items():
        lines = [line for line in state["invoice_lines"].values() if line["invoice_id"] == invoice_id]
        seen_requests = set()
        pairs = set()
        for line in lines:
            repeated = seen_requests.intersection(line["request_ids"])
            if invoice_id.startswith("INV-BG-"):
                assert not repeated, (invoice_id, repeated)
            seen_requests.update(line["request_ids"])
            pair = (line["description"], line["amount_cents"])
            if pair in pairs:
                duplicates.add(invoice["account_id"])
            pairs.add(pair)
            if invoice_id.startswith("INV-BG-"):
                if line["product"] not in {"hosted_api", "embeddings", "batch"}:
                    assert not line["request_ids"]
                    continue
                assert line["request_ids"]
                for request_id in line["request_ids"]:
                    usage = state["usage"][request_id]
                    assert usage["line_id"] == line["line_id"]
                    assert line["period_start"] <= usage["at"][:10] <= line["period_end"]
    assert duplicates == allowed
    saltmarsh = next(
        invoice for invoice in state["invoices"].values()
        if invoice["account_id"] == "saltmarsh-data"
    )
    saltmarsh_lines = [
        line for line in state["invoice_lines"].values()
        if line["invoice_id"] == saltmarsh["invoice_id"]
    ]
    repeated = {}
    for line in saltmarsh_lines:
        repeated.setdefault((line["description"], line["amount_cents"]), []).append(line)
    lookalikes = next(lines for lines in repeated.values() if len(lines) > 1)
    assert not set(lookalikes[0]["request_ids"]).intersection(lookalikes[1]["request_ids"])


def test_billed_usage_points_to_listed_metered_invoice_line():
    state = run_agent.load_db("R1")
    metered_products = {"hosted_api", "embeddings", "batch"}
    for request_id, usage in state["usage"].items():
        if usage["line_id"] is None:
            continue
        line = state["invoice_lines"][usage["line_id"]]
        assert line["product"] in metered_products
        assert line["period_start"] <= usage["at"][:10] <= line["period_end"]
        assert request_id in line["request_ids"]


def _development_state() -> dict:
    return practice_seed("R1")


def test_development_data_is_valid_and_disjoint_from_graded_data_and_scenarios():
    practice, graded = _development_state(), run_agent.load_db("R1")
    billing_store.validate(practice)
    key_fields = {
        "account_id": "accounts",
        "org": "accounts",
        "email": "contacts",
        "invoice_id": "invoices",
        "line_id": "invoice_lines",
        "request_id": "usage",
        "grant_id": "credit_grants",
        "contract_id": "contracts",
    }
    for field, table in key_fields.items():
        practice_values = {row[field] for row in practice[table].values()}
        graded_values = {row[field] for row in graded[table].values()}
        assert practice_values.isdisjoint(graded_values), field

    scenarios = json.dumps(json.loads((TASK / "tests" / "data" / "billing_scenarios.json").read_text()))
    assert all(row["org"] not in scenarios for row in practice["accounts"].values())
    assert {
        account_id: row["org"] for account_id, row in practice["accounts"].items()
    } == {
        "practice-alderwick": "alderwick-labs",
        "practice-brackenvale": "brackenvale-ai",
        "practice-copperfern": "copperfern-data",
        "practice-dawnmere": "dawnmere-cloud",
        "practice-emberquarry": "emberquarry-systems",
        "practice-fablepeak": "fablepeak-analytics",
        "practice-glenhaven": "glenhaven-ops",
        "practice-hearthstone": "hearthstone-metrics",
        "practice-ironbark": "ironbark-robotics",
        "practice-juniper": "juniper-notes",
        "practice-kestrel": "kestrel-vision",
        "practice-larchmont": "larchmont-studio",
    }


def test_development_manifest_selectors_exist_in_development_rows():
    state = _development_state()
    tables = {
        "account_id": "accounts",
        "invoice_id": "invoices",
        "line_ids": "invoice_lines",
        "request_ids": "usage",
        "contract_id": "contracts",
        "grant_id": "credit_grants",
    }
    manifest = practice_cases("R1")
    assert [case["id"] for case in manifest["cases"]] == [
        "duplicate_180",
        "duplicate_900",
        "lookalike_180",
        "marketplace_usage",
        "sla_contract",
        "contract_duplicate_60",
        "ledger_balance",
        "gcp_marketplace_usage",
        "free_quota_402",
    ]
    for case in manifest["cases"]:
        for selector, table in tables.items():
            if selector not in case:
                continue
            values = case[selector] if isinstance(case[selector], list) else [case[selector]]
            assert values
            assert set(values) <= set(state[table])
    manifest["cases"][0]["line_ids"].clear()
    assert practice_cases("R1")["cases"][0]["line_ids"]


def test_development_data_contains_the_planted_situations():
    state = _development_state()
    lines_by_invoice: dict[str, list[dict]] = defaultdict(list)
    for line in state["invoice_lines"].values():
        lines_by_invoice[line["invoice_id"]].append(line)

    for amount in (18_000, 32_000, 90_000):
        assert any(
            left["description"] == right["description"]
            and left["amount_cents"] == right["amount_cents"] == amount
            and set(left["request_ids"]) & set(right["request_ids"])
            for lines in lines_by_invoice.values()
            for index, left in enumerate(lines)
            for right in lines[index + 1:]
        )

    lookalike = lines_by_invoice["PRACTICE-LOOKALIKE"]
    assert lookalike[0]["description"] == lookalike[1]["description"]
    assert lookalike[0]["amount_cents"] == lookalike[1]["amount_cents"] == 18_000
    assert set(lookalike[0]["request_ids"]).isdisjoint(lookalike[1]["request_ids"])

    marketplace = next(
        account_id for account_id, row in state["accounts"].items()
        if row["billing_channel"] == "aws_marketplace"
    )
    assert not any(row["account_id"] == marketplace for row in state["invoices"].values())
    assert any(row["account_id"] == marketplace for row in state["usage"].values())
    assert any(row["sla"] for row in state["contracts"].values())
    assert any(
        row["kind"] == "promotional" and row["remaining_cents"] == 0
        and row["expires_on"] < "2026-09-15"
        for row in state["credit_grants"].values()
    )
    assert any(row["line_id"] is None for row in state["usage"].values())
    direct_usage_counts = {
        account_id: sum(row["account_id"] == account_id for row in state["usage"].values())
        for account_id, account in state["accounts"].items()
        if account["billing_channel"] == "direct"
    }
    assert max(direct_usage_counts.values()) > billing_store.PAGE_SIZE


def test_development_billing_insert_query_is_instance_local_and_usage_is_paged():
    first = local_console("R0")
    account = first.query("accounts", {"account_id": "practice-alderwick"})["rows"][0]
    case = first.insert("cases", {
        "account_id": account["account_id"],
        "summary": "Practice console isolation check",
        "status": "draft",
    })
    assert first.query("cases", {"case_id": case["case_id"]})["rows"] == [case]

    first_page = first.query("usage", {"account_id": "practice-fablepeak"})
    assert len(first_page["rows"]) == billing_store.PAGE_SIZE
    assert first_page["next_cursor"] is not None
    second_page = first.query("usage", {"account_id": "practice-fablepeak"}, first_page["next_cursor"])
    assert len(first_page["rows"]) + len(second_page["rows"]) == 26

    second = local_console("R0")
    assert second.query("cases", {"case_id": case["case_id"]})["rows"] == []


def test_development_billing_r1_uses_money_objects_and_requires_priority():
    billing = local_console("R1")
    invoice = billing.query("invoices", {"invoice_id": "PRACTICE-DUP-180"})["rows"][0]
    assert invoice["total"] == {"value": "360.00", "currency": "USD"}
    line = billing.query("invoice_lines", {"line_id": "PRACTICE-DUP-180/L1"})["rows"][0]
    assert line["amount"] == {"value": "180.00", "currency": "USD"}
    with pytest.raises(ClientBillingError, match="priority is required: one of normal, urgent"):
        billing.insert("cases", {
            "account_id": "practice-brackenvale",
            "team": "billing_ops",
            "summary": "Review a support request",
            "status": "escalated",
        })


def test_advance_publishes_the_r1_framework_read_only(tmp_path, monkeypatch):
    release_path = tmp_path / "release"
    release_path.write_text("R0\n")
    framework = tmp_path / "framework"
    shutil.copytree(R0_FRAMEWORK, framework)
    overlay = tmp_path / "overlay"
    shutil.copytree(R1_OVERLAY, overlay)
    monkeypatch.setattr(advance, "RELEASE", release_path)
    monkeypatch.setattr(advance, "FRAMEWORK", framework)
    monkeypatch.setattr(advance, "OVERLAY", overlay)
    monkeypatch.setattr(advance, "SEALED", ())
    monkeypatch.setattr(advance, "reveal", lambda *_args: 0)
    monkeypatch.setattr(advance.os, "geteuid", lambda: 0)
    monkeypatch.setattr(advance.grp, "getgrnam", lambda _name: SimpleNamespace(gr_gid=0))

    assert advance.main() == 0
    assert release_path.read_text() == "R1\n"
    assert json.loads((framework / "client_api/schema.json").read_text()) == billing_store.Billing(
        release="R1"
    ).describe()
    assert json.loads((framework / "client_api/practice_cases.json").read_text()) == practice_cases("R1")
    probes = json.loads((framework / "retrieval_probes.json").read_text())["probes"]
    assert [p["id"] for p in probes if p["releases"] == ["R1"]] == ["b7", "b8"]
    for name in ("client_api/schema.json", "client_api/practice_cases.json", "retrieval_probes.json"):
        assert stat.S_IMODE((framework / name).stat().st_mode) == 0o444


def test_r1_overlay_extends_the_r0_framework():
    r0_accounts = set(practice_seed("R0")["accounts"])
    r0_cases, r1_cases = practice_cases("R0")["cases"], practice_cases("R1")["cases"]
    assert {case["account_id"] for case in r0_cases} <= r0_accounts
    assert r1_cases[: len(r0_cases)] == r0_cases
    assert {case["account_id"] for case in r1_cases} - r0_accounts
    r0_probes = json.loads((R0_FRAMEWORK / "retrieval_probes.json").read_text())["probes"]
    r1_probes = json.loads((R1_OVERLAY / "framework/retrieval_probes.json").read_text())["probes"]
    assert all("R0" in probe["releases"] for probe in r0_probes)
    assert r1_probes[: len(r0_probes)] == r0_probes


def test_development_billing_serves_the_current_release_over_the_socket(tmp_path):
    release_path = tmp_path / "release"
    release_path.write_text("R0\n")
    server = billing_store.open_practice_server(
        str(tmp_path / "practice.sock"),
        release_file=release_path,
        r1_delta=R1_OVERLAY / "development_seed.json",
        grant_harbor=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        r0 = development_billing(str(tmp_path / "practice.sock"))
        assert "credit_ledger" not in r0.describe()
        assert r0.query("accounts", {"account_id": "practice-ironbark"})["rows"] == []
        release_path.write_text("R1\n")
        r1 = development_billing(str(tmp_path / "practice.sock"))
        assert "credit_ledger" in r1.describe()
        assert r1.query("accounts", {"account_id": "practice-ironbark"})["rows"]
    finally:
        server.shutdown()
        server.server_close()


def test_a_matching_credit_passes_every_code_check():
    case = CREDIT_CASE
    actual = record(query("accounts", {"account_id": COPPERLEAF}), credit(memo="custom memo"))
    result = Result(case_id="a0-23", termination=Termination.CUSTOMER_STOP, billing=as_billing(actual))
    graded = grade.grade(Scenario.model_validate(case), result)
    assert graded.passed and {row.kind for row in graded.checks} == {"db", "action"}


def test_unfinished_run_fails_billing_rows():
    case = CREDIT_CASE
    result = Result(
        case_id="a0-23",
        termination=Termination.MAX_STEPS,
        billing=as_billing(record(query("accounts", {"account_id": COPPERLEAF}), credit())),
    )
    graded = grade.grade(Scenario.model_validate(case), result)
    billing_rows = [row for row in graded.checks if row.kind in {"db", "action"}]
    assert len(billing_rows) == 2
    assert all(not row.passed for row in billing_rows)


def test_scenario_without_db_has_no_billing_rows():
    case = Scenario(id="no-db")
    result = Result(case_id="no-db", termination=Termination.CUSTOMER_STOP)
    graded = grade.grade(case, result)
    assert not any(row.kind in {"db", "action"} for row in graded.checks)


TOOLS = '''
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

class Tools(ToolKitBase):
    @is_tool(ToolType.READ)
    def account(self, account_id: str) -> dict:
        """Query account records.

        Args:
            account_id: The account identifier.
        """
        return self.billing.query("accounts", {"account_id": account_id})

    @is_tool(ToolType.WRITE)
    def credit_line(self, account_id: str, invoice_id: str, line_id: str, amount_cents: int) -> dict:
        """Insert a credit note.

        Args:
            account_id: The account identifier.
            invoice_id: The invoice identifier.
            line_id: The invoice line identifier.
            amount_cents: The amount in cents.
        """
        return self.billing.insert("credit_notes", {
            "account_id": account_id,
            "invoice_id": invoice_id,
            "line_ids": [line_id],
            "amount_cents": amount_cents,
            "reason": "duplicate",
            "memo": "",
        })
'''


def test_run_case_candidate_uses_generic_billing_protocol(monkeypatch, tmp_path):
    turns = [
        AssistantMessage(tool_calls=[ToolCall("c1", "account", {"account_id": COPPERLEAF})]),
        AssistantMessage(tool_calls=[ToolCall("c2", "credit_line", {
            "account_id": COPPERLEAF,
            "invoice_id": INV_COPPERLEAF,
            "line_id": LINE_COPPERLEAF_L3,
            "amount_cents": 34000,
        })]),
        AssistantMessage(content="I credited $340 for the duplicate line."),
    ]

    def generate(model, messages, tools, tool_choice, **kwargs):
        turn = turns.pop(0)
        turn.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return turn

    monkeypatch.setattr(gateway, "generate", generate)
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    workspace = tmp_path / "workspace"
    shutil.copytree(TASK / "environment" / "app" / "workspace", workspace)
    (workspace / "tools.py").write_text(TOOLS)
    result = run_agent.run_case(
        Scenario.model_validate(CREDIT_CASE),
        workspace,
        tmp_path,
        ScriptedCustomer("The duplicate is on L3.", "Thanks. ###STOP###"),
        release="R0",
    )
    assert (result.termination, result.error) == ("customer_stop", None)
    assert [(call.op, call.table, call.ok) for call in result.billing.log] == [
        ("query", "accounts", True),
        ("insert", "credit_notes", True),
    ]
    assert all(row.passed for row in billing_rows(CREDIT_CASE, result.billing).values())


def test_credit_gold_accepts_either_duplicate_line_and_ignores_memo_and_id():
    for line_id in (LINE_COPPERLEAF_L2, LINE_COPPERLEAF_L3):
        actual = record(
            query("accounts", {"account_id": COPPERLEAF}),
            credit(line_id=line_id, memo=f"custom memo for {line_id}"),
        )
        assert all(row.passed for row in billing_rows(CREDIT_CASE, actual).values())


def test_split_credit_fails_db_and_a_lookup_after_the_write_still_counts():
    split = record(
        query("accounts", {"account_id": COPPERLEAF}),
        credit(amount_cents=17000),
        credit(amount_cents=17000),
    )
    split_checks = billing_rows(CREDIT_CASE, split)
    assert split_checks["action"].passed
    assert not split_checks["db"].passed

    early = record(credit(), query("accounts", {"account_id": COPPERLEAF}))
    early_checks = billing_rows(CREDIT_CASE, early)
    assert early_checks["db"].passed and early_checks["action"].passed  # like hyper-tau, order is not graded

    rejected = record(
        query("accounts", {"account_id": COPPERLEAF}),
        credit(amount_cents=999999),
    )
    rejected_checks = billing_rows(CREDIT_CASE, rejected)
    assert not rejected["log"][-1].ok
    assert "no successful insert:credit_notes()" in rejected_checks["action"].problem


def test_unfiltered_accounts_query_is_rejected_before_it_can_count_as_a_lookup():
    result = record(query("accounts"), credit())
    checks = billing_rows(CREDIT_CASE, result)
    assert checks["db"].passed
    assert "no successful query:accounts(account_id=copperleaf-ai)" in checks["action"].problem
    assert result["log"][0].error == "query on accounts needs one of: account_id, org"
    assert result["log"][0].account_ids is None


def test_query_on_another_table_does_not_count_as_an_account_lookup():
    result = record(query("invoices", {"account_id": COPPERLEAF}), credit())
    checks = billing_rows(CREDIT_CASE, result)
    assert checks["db"].passed
    assert "no successful query:accounts(account_id=copperleaf-ai)" in checks["action"].problem
    assert result["log"][0].account_ids is None


def test_starter_and_r0_oracle_escalations_drift_on_r1_priority(
    monkeypatch,
):
    seed = run_agent.load_db("R1")
    starter_path = TASK / "environment" / "app" / "workspace" / "tools.py"
    oracle_path = TASK / "steps" / "build_r0" / "solution" / "workspace" / "tools.py"

    def tools(path: Path, name: str):
        monkeypatch.syspath_prepend(str(path.parent))
        monkeypatch.delitem(sys.modules, "billing_rules", raising=False)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Tools()

    def client_for(store):
        position = 0

        def send(request):
            nonlocal position
            position += 1
            return {"ok": True, **store.handle(request, position)}

        return BillingClient(send)

    account_id = "tidewell-labs"
    r1_store = billing_store.Billing(seed, release="R1")
    starter = tools(starter_path, "starter_priority_tools")
    starter.billing = client_for(r1_store)
    with pytest.raises(ClientBillingError, match="priority is required: one of normal, urgent"):
        starter.escalate(account_id, "billing_ops", "Review the duplicate charge")

    escalation = {
        "account_id": account_id,
        "team": "billing_ops",
        "summary": "Review the duplicate charge",
        "details": {},
        "unresolved": "The duplicate charge needs Billing Ops review.",
        "routing_rule": (
            "Disputed or duplicated line on a direct Redwood invoice | "
            "Billing Ops | Invoice number, the line(s), UTC window, request IDs"
        ),
    }
    r0_store = billing_store.Billing(seed, release="R0")
    oracle = tools(oracle_path, "r0_oracle_priority_tools_R0")
    oracle.billing = client_for(r0_store)
    case = oracle.escalate_case(**escalation)
    assert case["status"] == "escalated"
    assert r0_store.state["cases"][case["case_id"]]["priority"] is None
    assert r0_store.state["cases"][case["case_id"]]["details"]["routing_rule"]

    oracle = tools(oracle_path, "r0_oracle_priority_tools_R1")
    oracle.billing = client_for(billing_store.Billing(seed, release="R1"))
    with pytest.raises(ClientBillingError, match="priority is required: one of normal, urgent"):
        oracle.escalate_case(**escalation)


def test_dispute_gold_accepts_either_duplicate_line_and_checks_reason():
    case = dispute_case(
        "tidewell-labs", "INV-2026-0831-114", "INV-2026-0831-114/L2", ["req_a91", "req_a92"],
        "INV-2026-0831-114/L3", [escalated_case_action("tidewell-labs", "billing_ops")],
    )
    for line_id in ("INV-2026-0831-114/L2", "INV-2026-0831-114/L3"):
        actual = record(
            query("accounts", {"account_id": "tidewell-labs"}),
            dispute("tidewell-labs", "INV-2026-0831-114", line_id, ["req_a91", "req_a92"]),
            insert("cases", case_row(
                account_id="tidewell-labs",
                team="billing_ops",
                dispute_id="DSP-1",
            )),
        )
        result = billing_rows(case, actual)
        assert all(row.passed for row in result.values()), line_id

    wrong_reason = record(
        query("accounts", {"account_id": "tidewell-labs"}),
        dispute("tidewell-labs", "INV-2026-0831-114", "INV-2026-0831-114/L3",
                ["req_a91", "req_a92"], reason="incorrect_amount"),
        insert("cases", case_row(
            account_id="tidewell-labs",
            team="billing_ops",
            dispute_id="DSP-1",
        )),
    )
    reason_checks = billing_rows(case, wrong_reason)
    assert not reason_checks["db"].passed and reason_checks["action"].passed


def test_r1_dispute_is_valid_but_credit_of_three_hundred_is_not():
    case = dispute_case(
        "marrowline-analytics", "INV-2027-0331-118", "INV-2027-0331-118/L2", ["req_b71", "req_b72"],
        "INV-2027-0331-118/L3",
        [escalated_case_action("marrowline-analytics", "billing_ops", priority="normal")],
    )
    actual = record(
        query("accounts", {"account_id": "marrowline-analytics"}),
        dispute(
            "marrowline-analytics",
            "INV-2027-0331-118",
            "INV-2027-0331-118/L3",
            ["req_b71", "req_b72"],
        ),
        insert("cases", case_row(
            account_id="marrowline-analytics",
            team="billing_ops",
            priority="normal",
            dispute_id="DSP-1",
        )),
        release="R1",
    )
    assert all(row.passed for row in billing_rows(case, actual, "R1").values())
    credit_actual = record(
        query("accounts", {"account_id": "marrowline-analytics"}),
        credit(
            "marrowline-analytics",
            "INV-2027-0331-118",
            "INV-2027-0331-118/L3",
            amount_cents=30000,
        ),
        release="R1",
    )
    failed = {name for name, row in billing_rows(case, credit_actual, "R1").items()
              if not row.passed}
    assert failed == {"db"}


def test_console_errors_carry_a_stable_code_through_the_client():
    console = local_console("R1")
    attempts = {
        "not_found": lambda: console.query("missing", {"account_id": COPPERLEAF}),
        "read_only": lambda: console.insert("accounts", {"account_id": "x"}),
        "unknown_field": lambda: console.insert("credit_notes", {"amount_cents": 100}),
    }
    for code, attempt in attempts.items():
        with pytest.raises(ClientBillingError) as caught:
            attempt()
        assert caught.value.code == code
        assert str(caught.value).startswith(f"{code}: ")

    r1 = billing_store.Billing(run_agent.load_db("R1"), release="R1")
    with pytest.raises(BillingError) as caught:
        r1.handle(insert("cases", {
            "account_id": COPPERLEAF,
            "team": "billing_ops",
            "summary": "needs review",
            "status": "escalated",
        }), 1)
    assert caught.value.code == "missing_fields"
    assert r1.log[-1].error == "priority is required: one of normal, urgent"
    assert ClientBillingError("host rejected the request").code is None
