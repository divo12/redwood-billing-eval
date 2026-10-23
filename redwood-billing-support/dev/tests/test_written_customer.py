"""The written customer: a model words the script's lines; code keeps order, facts, consent and endings."""
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tests"
sys.path[:0] = [str(TESTS), str(TESTS.parent / "environment")]

from grade.scenarios import Scenario  # noqa: E402
from run import gateway, written_customer  # noqa: E402
from run.customer import STOP_MESSAGE, TRANSFER_MESSAGE  # noqa: E402

CASE = Scenario.model_validate({
    "id": "w-1",
    "scenario": {"persona": "Terse."},
    "customer": {"opening": "Invoice INV-9 line L1 is $2,500 but our export is 20% less. Org acme-x.",
                 "agenda": ["Will someone review it?"],
                 "facts": {"org_id": "acme-x", "request_ids": "req_w07"}},
})


def _customer(monkeypatch, tmp_path, answers):
    monkeypatch.setenv(written_customer.CUSTOMER_CACHE, str(tmp_path / "turns.jsonl"))
    calls = []

    def complete(model, instructions, text, effort="low"):
        calls.append(text)
        return answers.pop(0)

    monkeypatch.setattr(gateway, "complete", complete)
    return written_customer.WrittenCustomer(CASE, "test-model"), calls


def test_a_faithful_rewording_is_used_and_cached(monkeypatch, tmp_path):
    customer, calls = _customer(monkeypatch, tmp_path, ["hi - INV-9 L1 shows $2,500, export is 20% lower. org acme-x"])
    assert customer.reply("Hi!", handed_off=False).content.startswith("hi - INV-9")
    again, again_calls = _customer(monkeypatch, tmp_path, [])
    assert again.reply("Hi!", handed_off=False).content.startswith("hi - INV-9") and not again_calls  # cache
    assert customer.handle({"op": "ask", "fields": ["request_ids"]}, 0) == {"answers": {"request_ids": "req_w07"}}


def test_a_rewording_that_drops_a_value_falls_back_to_the_line(monkeypatch, tmp_path):
    customer, _ = _customer(monkeypatch, tmp_path, ["our invoice looks too high, can you check?"])
    assert customer.reply("Hi!", handed_off=False).content == CASE.customer.opening
    assert customer.calls[-1].answer["fallback"] is True


def test_a_model_fault_falls_back_and_endings_stay_scripted(monkeypatch, tmp_path):
    customer, _ = _customer(monkeypatch, tmp_path, [None, None])
    assert customer.reply("Hi!", handed_off=False).content == CASE.customer.opening
    assert customer.reply("Sure.", handed_off=False).content == "Will someone review it?"
    assert customer.reply("Yes.", handed_off=False).content == STOP_MESSAGE
    assert customer.reply("Handed off.", handed_off=True).content == TRANSFER_MESSAGE
