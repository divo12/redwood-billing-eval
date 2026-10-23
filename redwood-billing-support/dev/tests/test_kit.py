"""Every redwood_kit component a builder can use, run through the real candidate process with scripted models."""
from workspaces import copy_starter  # noqa: E402
import importlib.util
import json
import shutil
import sys
import typing
from pathlib import Path

import pytest

TASK = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from grade.scenarios import Scenario
from redwood_kit.billing import Billing, BillingError
from redwood_kit.billing.schema import SCHEMA, Table
from redwood_kit.messages import AssistantMessage, ToolCall
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool
from run import gateway, run_agent
from test_run_agent import ScriptedCustomer

KIT = "from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool\n"
TEAMS = SCHEMA[Table.CASES].field("team").enum or ()


def run(monkeypatch, tmp_path, turns, customer, files=None):
    """Converse with scripted model replies; returns the result and every model call's (model, kwargs, messages)."""
    calls = []

    def generate(model, messages, tools, tool_choice, **kwargs):
        calls.append((model, kwargs, messages, [t["name"] for t in tools or []]))
        turn = turns.pop(0)
        turn.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return turn

    monkeypatch.setattr(gateway, "generate", generate)
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    workspace = tmp_path / "workspace"
    copy_starter(workspace)
    for name, text in (files or {}).items():
        (workspace / name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_text(text)
    return run_agent.run_case(Scenario(id="t"), workspace, tmp_path, customer), calls


def tool_calls(*calls):
    return AssistantMessage(tool_calls=[ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)])


POLICY_TOOLS = KIT + '''
class Tools(ToolKitBase):
    @is_tool(ToolType.READ)
    def policy_section(self, section: int) -> str:
        """Return one numbered section of the billing policy.

        Args:
            section: The section number.
        """
        return f"Section {section}: the authoritative final section." if section == 2 else "See section 2."
'''


def test_tool_results_reach_the_next_model_call_and_are_saved_in_evidence(monkeypatch, tmp_path):
    turns = [tool_calls(("policy_section", {"section": 1})),
             tool_calls(("policy_section", {"section": 2})),
             AssistantMessage(content="Found the policy.")]
    result, _ = run(monkeypatch, tmp_path, turns, ScriptedCustomer("Find it.", "Thanks. ###STOP###"),
                    {"tools.py": POLICY_TOOLS})
    outputs = [json.loads(m["content"]) for m in result.messages if m["role"] == "tool"]
    assert outputs == ["See section 2.", "Section 2: the authoritative final section."]
    assert "authoritative final section" in json.dumps(result.model_calls[-1].messages)
    assert result.termination == "customer_stop"


CASE_TOOLS = KIT + '''
class Tools(ToolKitBase):
    @is_tool(ToolType.READ)
    def query(self, table: str, where: dict | None = None, cursor: str | None = None) -> dict:
        """Query billing records.

        Args:
            table: The table to query.
            where: Field filters.
            cursor: The next page cursor.
        """
        return self.billing.query(table, where, cursor)

    @is_tool(ToolType.WRITE)
    def insert(self, table: str, row: dict) -> dict:
        """Insert a billing record.

        Args:
            table: The table to update.
            row: The record to insert.
        """
        return self.billing.insert(table, row)

    @is_tool(ToolType.WRITE)
    def update(self, table: str, row_id: str, changes: dict) -> dict:
        """Update a billing record.

        Args:
            table: The table to update.
            row_id: The record identifier.
            changes: The fields to change.
        """
        return self.billing.update(table, row_id, changes)
'''


def escalated(result):
    return [case for case in result.billing.state["cases"].values() if case["status"] == "escalated"]


def test_an_escalation_is_final_and_keeps_the_case_as_it_was_sent(monkeypatch, tmp_path):
    turns = [tool_calls(("insert", {"table": "cases", "row": {
                 "account_id": "copperleaf-ai", "summary": "Duplicate charge", "details": {"invoice": "INV-1"},
                 "status": "draft",
             }})),
             tool_calls(("update", {"table": "cases", "row_id": "CASE-1",
                                    "changes": {"team": "nowhere", "status": "escalated"}}),
                        ("update", {"table": "cases", "row_id": "CASE-9",
                                    "changes": {"team": "finance", "summary": "Missing case",
                                                "status": "escalated"}})),
             tool_calls(("update", {"table": "cases", "row_id": "CASE-1",
                                    "changes": {"team": "billing_ops", "status": "escalated"}})),
             tool_calls(("query", {"table": "cases", "where": {"case_id": "CASE-1"}})),
             AssistantMessage(content="Billing will review it.")]
    result, _ = run(monkeypatch, tmp_path, turns, ScriptedCustomer("I was charged twice.", "Thanks. ###STOP###"),
                    {"tools.py": CASE_TOOLS})
    assert result.termination == "customer_stop"
    [case] = escalated(result)
    assert {key: case[key] for key in ("case_id", "team", "at", "summary", "details")} == {
        "case_id": "CASE-1", "team": "billing_ops", "at": 8, "summary": "Duplicate charge",
        "details": {"invoice": "INV-1"}}  # the later note did not change the handed-off case
    errors = [m["content"] for m in result.messages if m["role"] == "tool" and m["error"]]
    assert "billing_ops" in errors[0] and "billing_engineering" in errors[0]
    assert "unknown case_id 'CASE-9'" in errors[1]
    assert "already handed to billing_ops as CASE-1" in errors[2]


def test_billing_client_uses_the_generic_protocol_and_wraps_errors():
    sent = []

    def send(request):
        sent.append(request)
        if request["op"] == "query":
            raise RuntimeError("unknown table 'missing'")
        return {"ok": True, "rows": [], "next_cursor": None}

    billing = Billing(send)
    assert billing.describe() == {"rows": [], "next_cursor": None}
    with pytest.raises(BillingError, match="unknown table"):
        billing.query("missing")
    assert sent == [
        {"call": "billing", "op": "describe"},
        {"call": "billing", "op": "query", "table": "missing", "where": None, "cursor": None},
    ]


SUBAGENT_TOOLS = KIT + '''from redwood_kit.subagents import SubagentTools
from redwood_kit.skills import SkillTools


class Tools(ToolKitBase, SkillTools, SubagentTools):
    @is_tool(ToolType.WRITE)
    def escalate(self, account_id: str, team: str, summary: str) -> dict:
        """Insert an escalated case.

        Args:
            account_id: The account identifier.
            team: The team.
            summary: A case summary.
        """
        self.billing.query("accounts", {"account_id": account_id})
        return self.billing.insert("cases", {
            "account_id": account_id,
            "team": team,
            "summary": summary,
            "details": {},
            "dispute_id": None,
            "status": "escalated",
        })
'''
ROUTER = "---\nname: router\ndescription: Routes credit requests.\ntools: escalate\n---\nYou route credit requests."
SKILL = "---\nname: credits\ndescription: When a credit is requested.\n---\nNever promise a credit."


def test_a_subagents_escalation_is_recorded_and_it_uses_the_default_model(monkeypatch, tmp_path):
    turns = [tool_calls(("load_skill", {"name": "credits"})),
             tool_calls(("load_skill", {"name": "refunds"})),
             tool_calls(("delegate", {"subagent": "router", "task": "Org acme wants a $500 credit."})),
             tool_calls(("escalate", {
                 "account_id": "copperleaf-ai", "team": "finance", "summary": "acme asks for a $500 credit",
             })),  # the subagent
             AssistantMessage(content="Sent to finance."),  # the subagent's answer
             AssistantMessage(content="Finance will review your request.")]
    files = {"tools.py": SUBAGENT_TOOLS, "subagents/router.md": ROUTER, "skills/credits/SKILL.md": SKILL}
    result, calls = run(monkeypatch, tmp_path, turns, ScriptedCustomer("I want $500.", "OK. ###STOP###"), files)
    assert result.termination == "customer_stop"
    assert [(c["team"], c["summary"]) for c in escalated(result)] == [("finance", "acme asks for a $500 credit")]
    tool_results = [m["content"] for m in result.messages if m["role"] == "tool"]
    assert "Never promise a credit." in tool_results[0] and "unknown skill 'refunds'" in tool_results[1]
    assert '"answer": "Sent to finance."' in tool_results[2]
    model, kwargs, messages, tools = calls[3]  # the subagent's first call
    assert (model, kwargs, tools) == ("openai/gpt-6-luna", {}, ["escalate"])
    assert messages[0].content == "You route credit requests."


def test_a_subagent_that_never_answers_is_a_failed_tool_call(monkeypatch, tmp_path):
    router = ROUTER.replace("tools: escalate", "tools: escalate\nmax_turns: 1")
    turns = [tool_calls(("delegate", {"subagent": "router", "task": "Route it."})),
             tool_calls(("escalate", {"account_id": "copperleaf-ai", "team": "sre", "summary": "x"})),
             # the subagent, out of turns afterwards
             AssistantMessage(content="Sorry, I couldn't route that.")]
    result, _ = run(monkeypatch, tmp_path, turns, ScriptedCustomer("Route me.", "OK. ###STOP###"),
                    {"tools.py": SUBAGENT_TOOLS, "subagents/router.md": router})
    assert result.termination == "customer_stop"
    assert "did not answer within 1 turns" in result.messages[3]["content"] and result.messages[3]["error"]


HOOKS = '''from redwood_kit.hooks import Blocked, HookKitBase, hook


class Hooks(HookKitBase):
    @hook("user_message")
    def blank(self, text):
        return "" if "secret" in text else None

    @hook("pre_tool", match="escalate")
    def needs_invoice(self, tool, args):
        if "INV-" not in args["summary"]:
            raise Blocked("record the invoice number first")

    @hook("pre_tool", match="ping")
    def reset(self, tool, args):
        return {}
'''
PING_TOOLS = SUBAGENT_TOOLS + '''
    @is_tool(ToolType.READ)
    def ping(self, value: str = "default") -> dict:
        """Echo a value.

        Args:
            value: Anything.
        """
        print("ping called")  # must not reach the protocol
        return {"value": value}
'''


def test_hooks_can_block_a_handoff_and_replace_with_empty_values(monkeypatch, tmp_path):
    turns = [tool_calls(("escalate", {"team": "billing_ops", "summary": "Charged twice"}), ("ping", {"value": "x"})),
             AssistantMessage(content="What is the invoice number?")]
    result, calls = run(monkeypatch, tmp_path, turns, ScriptedCustomer("my secret key", "Never mind. ###STOP###"),
                        {"tools.py": PING_TOOLS, "hooks.py": HOOKS})
    assert result.termination == "customer_stop" and escalated(result) == []
    blocked, ping = (m for m in result.messages if m["role"] == "tool")
    assert blocked["error"] and "Blocked: record the invoice number first" in blocked["content"]
    assert ping["content"] == '{"value": "default"}'
    assert calls[0][2][-1].content == ""  # the user_message hook's empty replacement reached the model


PRINTING_AGENT = '''from redwood_kit.agent_context import get_agent_context
from redwood_kit.messages import AssistantMessage


class Agent:
    def __init__(self):
        print("building the agent")
        self.billing = get_agent_context().billing

    def get_init_state(self, message_history=None):
        return []

    def generate_next_message(self, message, state):
        print("turn", message.content)
        self.billing.query("accounts", {"account_id": "copperleaf-ai"})
        receipt = self.billing.insert("cases", {
            "account_id": "copperleaf-ai",
            "team": "account_team",
            "summary": "From agent.py",
            "details": {"org": "acme"},
            "dispute_id": None,
            "status": "escalated",
        })
        return AssistantMessage(content=f"Escalated: {receipt['status']}"), state


def create_agent():
    return Agent()
'''


def test_workspace_prints_go_to_the_log_and_agent_code_can_use_billing(monkeypatch, tmp_path):
    result, _ = run(monkeypatch, tmp_path, [], ScriptedCustomer("Hello.", "Thanks. ###STOP###"),
                    {"agent.py": PRINTING_AGENT})
    assert (result.termination, result.reply) == ("customer_stop", "Escalated: escalated")
    assert escalated(result)[0]["team"] == "account_team"
    assert "building the agent" in (tmp_path / "agent_t.log").read_text()


def test_tool_schemas_cover_literal_optional_dict_and_defaults():
    class Tools(ToolKitBase):
        @is_tool(ToolType.WRITE)
        def route(self, team: typing.Literal["finance", "legal"], note: str | None = None,
                  details: dict[str, str] | None = None, count: int = 1) -> dict:
            """Route a case.

            Args:
                team: Where it goes.
                note: Optional note.
                details: Named details.
                count: How many.
            """

    [tool] = Tools().get_tools()
    props = tool.parameters["properties"]
    assert tool.parameters["required"] == ["team"] and props["team"]["enum"] == ["finance", "legal"]
    assert props["details"]["anyOf"][0]["additionalProperties"] == {"type": "string"}
    assert props["count"]["default"] == 1 and props["team"]["description"] == "Where it goes."


EMBED_TOOLS = KIT + '''
class Tools(ToolKitBase):
    @is_tool(ToolType.READ)
    def search(self, query: str) -> dict:
        """Look up a billing note.

        Args:
            query: What to look up.
        """
        vectors = self.gateway.embed(model="sentence-transformers/all-MiniLM-L6-v2", inputs=[query])
        return {"dimensions": len(vectors[0])}
'''


def test_a_tool_embeds_through_the_gateway(monkeypatch, tmp_path):
    def embed(model, inputs, **kwargs):
        assert (model, inputs, kwargs) == ("sentence-transformers/all-MiniLM-L6-v2", ["refund window"], {})
        return [[0.1, 0.2, 0.3]], 0

    monkeypatch.setattr(gateway, "embed", embed)
    turns = [tool_calls(("search", {"query": "refund window"})), AssistantMessage(content="The window is 30 days.")]
    result, _ = run(monkeypatch, tmp_path, turns, ScriptedCustomer("What is the refund window?", "Thanks. ###STOP###"),
                    {"tools.py": EMBED_TOOLS})
    assert result.termination == "customer_stop"
    assert '"dimensions": 3' in next(m["content"] for m in result.messages if m["role"] == "tool")
    assert [c.model_dump() for c in result.embedding_calls] == [
        {"model": "sentence-transformers/all-MiniLM-L6-v2", "kwargs": {}, "inputs": 1, "prompt_tokens": 0}
    ]


def test_the_host_rejects_embedding_calls_outside_the_contract():
    host = run_agent.Candidate.__new__(run_agent.Candidate)
    host.embedding_calls = []
    refused = [
        ({"model": "text-embedding-ada-002", "inputs": ["policy"], "kwargs": {}}, "not allowed"),
        ({"model": "sentence-transformers/all-MiniLM-L6-v2", "inputs": ["  "], "kwargs": {}}, "non-empty"),
        ({"model": "sentence-transformers/all-MiniLM-L6-v2", "inputs": "policy", "kwargs": {}}, "non-empty"),
        ({"model": "sentence-transformers/all-MiniLM-L6-v2", "inputs": ["policy"], "kwargs": {"dimensions": 3}}, "unsupported"),
    ]
    for request, problem in refused:
        with pytest.raises(ValueError, match=problem):
            host._serve_embed(request)
    assert host.embedding_calls == []


def test_build_time_embed_uses_the_manifest_and_keeps_input_order(monkeypatch, tmp_path):
    from redwood_kit import embeddings

    manifest = tmp_path / "deployment_manifest.json"
    manifest.write_text(json.dumps({"allowed_embedding_models": [{"model": "sentence-transformers/all-MiniLM-L6-v2", "constraints": {}}]}))
    monkeypatch.setattr(embeddings, "MANIFEST", manifest)
    monkeypatch.setattr(embeddings, "encode", lambda model, texts: [[float(len(t)), 0.0] for t in texts])
    assert embeddings.embed(model="sentence-transformers/all-MiniLM-L6-v2", inputs=["a", "bb"]) == [[1.0, 0.0], [2.0, 0.0]]
    with pytest.raises(ValueError, match="not allowed"):
        embeddings.embed(model="text-embedding-3-small", inputs=["a"])
    with pytest.raises(ValueError, match="unsupported"):
        embeddings.embed(model="sentence-transformers/all-MiniLM-L6-v2", inputs=["a"], dimensions=8)


def test_the_oracle_routes_to_exactly_the_schema_teams(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # keep __pycache__ out of the solution tree
    paths = (TASK / "steps" / "build_r0" / "solution" / "workspace" / "tools.py",)
    for index, path in enumerate(paths):
        monkeypatch.syspath_prepend(str(path.parent))
        monkeypatch.delitem(sys.modules, "billing_rules", raising=False)
        spec = importlib.util.spec_from_file_location(f"oracle_tools_{index}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tool = next(tool for tool in module.Tools().get_tools() if tool.name == "escalate_case")
        assert tuple(tool.parameters["properties"]["team"]["enum"]) == TEAMS


def test_maintain_r1_overlay_replaces_billing_rules_and_indexes_the_new_records():
    workspace = TASK / "steps" / "maintain_r1" / "solution" / "workspace"
    assert {path.name for path in workspace.iterdir() if path.is_file()} == {
        "AGENTS.md", "billing_rules.py", "hooks.py", "system_prompt.md", "tools.py"}
    assert "DUPLICATE_CREDIT_LIMIT_CENTS = 25_000" in (workspace / "billing_rules.py").read_text()
    solve = (TASK / "steps" / "maintain_r1" / "solution" / "solve.sh").read_text()
    assert "cp -R /solution/workspace/. /app/workspace/" in solve
    assert "python /app/workspace/build_index.py --new /app/R" in solve
