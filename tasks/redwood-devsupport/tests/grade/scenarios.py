"""The typed shape of `billing_scenarios.json`.

Each scenario is one simulated conversation with hidden expectations, all
decided by code: the billing end state (`db`), console operations the agent
must have run (`actions`), values it must say (`communicate`), and values it
must never say (`never_say`). `load_scenarios` validates the file into `Scenario` objects
so the rest of the verifier works with attributes and enums instead of raw
dicts. The models are frozen and forbid unknown keys, so a typo in the JSON
fails at load time, not mid-run.
"""
from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from redwood_kit.jsontypes import JsonObject

SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "data" / "billing_scenarios.json"


class Release(StrEnum):
    """Which world the conversation runs against."""

    R0 = "R0"
    R1 = "R1"


class Step(StrEnum):
    """A benchmark step; each maps to one billing release."""

    BUILD_R0 = "build_r0"
    MAINTAIN_R1 = "maintain_r1"

    @property
    def release(self) -> Release:
        return _STEP_RELEASE[self]


_STEP_RELEASE = {Step.BUILD_R0: Release.R0, Step.MAINTAIN_R1: Release.R1}


class Purpose(StrEnum):
    BASELINE = "baseline"
    UPDATE = "update"
    STALENESS = "staleness"
    REGRESSION = "regression"


class OperationKind(StrEnum):
    QUERY = "query"
    INSERT = "insert"
    UPDATE = "update"
    WRITE = "write"  # insert or update


class Operation(BaseModel):
    """One console operation, written `"op:table"` in the JSON (`write:` means insert or update)."""

    model_config = ConfigDict(frozen=True)

    kind: OperationKind
    table: str

    @model_validator(mode="before")
    @classmethod
    def parse(cls, data: object) -> object:
        if isinstance(data, str):
            kind, separator, table = data.partition(":")
            if not separator or kind not in set(OperationKind):
                raise ValueError(f"expected 'op:table' with op in {[op.value for op in OperationKind]}, got {data!r}")
            return {"kind": kind, "table": table}
        return data

    def matches(self, op: str | None, table: str | None) -> bool:
        if self.kind is OperationKind.WRITE:
            return op in {OperationKind.INSERT, OperationKind.UPDATE} and table == self.table
        return op == self.kind and table == self.table

    def __str__(self) -> str:
        return f"{self.kind}:{self.table}"


class CustomerBrief(BaseModel):
    """The `scenario` block: who the simulated developer is and what they want."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Field order is load-bearing: the customer prompt dumps this model as JSON
    # and must stay byte-identical to the raw scenario block.
    today: str = ""
    persona: str = ""
    reason_for_contact: str = ""
    known_info: str = ""
    unknown_info: str = ""
    task_instructions: str = ""


class Action(BaseModel):
    """A console operation some successful call must match: hyper-tau's ACTION, compared on
    console operations instead of tool names, so builders keep their own tools.

        equals    a query: `account_id` is among the accounts it returned;
                  a write: the stored row has these field values
        contains  facts the receiving team sees: in the stored row or the conversation before it
        excludes  text no successful matching write may store (case, spaces and punctuation ignored)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: Operation
    equals: JsonObject = {}
    contains: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _query_compares_the_account_only(self) -> Action:
        compares_more = set(self.equals) - {"account_id"} or self.contains or self.excludes
        if self.op.kind is OperationKind.QUERY and compares_more:
            raise ValueError(f"{self.op}: a query action compares only equals.account_id")
        return self


class DbExpectation(BaseModel):
    """The billing end state: gold actions, or an accepted alternative, replayed on the seed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gold: list[JsonObject] = []
    alternatives: list[list[JsonObject]] = []
    scored: bool = True


class Scenario(BaseModel):
    """One conversation to run and the hidden checks that grade it."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str
    realization_of: str | None = None  # a rewording of this scenario id: averaged with it as one situation
    step: Step = Step.BUILD_R0
    rules: tuple[str, ...] = ()
    category: str = ""
    purpose: Purpose = Purpose.BASELINE
    brief: CustomerBrief = Field(default_factory=CustomerBrief, validation_alias="scenario",
                                 serialization_alias="scenario")
    db: DbExpectation | None = None
    actions: tuple[Action, ...] = ()
    communicate: tuple[tuple[str, ...], ...] = ()  # values the agent must tell the customer; any form of each counts
    never_say: tuple[tuple[str, ...], ...] = ()  # values no agent message may contain, in any listed form
    tougher_by: str = ""
    tags: tuple[str, ...] = ()


def load_scenarios(step: Step, only: frozenset[str] | None = None) -> list[Scenario]:
    """The scenarios of one step, optionally filtered to `only` ids."""
    document = json.loads(SCENARIOS_PATH.read_text())
    scenarios = [Scenario.model_validate(raw) for raw in document["scenarios"]]
    return [scenario for scenario in scenarios
            if scenario.step is step and (only is None or scenario.id in only)]
