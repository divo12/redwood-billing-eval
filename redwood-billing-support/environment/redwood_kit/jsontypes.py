"""Types for data that crosses a JSON boundary (files, the candidate wire protocol, the billing console)."""
from __future__ import annotations

from typing import TypeGuard

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]


def is_json(value: object) -> TypeGuard[Json]:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(is_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and is_json(item) for key, item in value.items())
    return False


def is_json_object(value: object) -> TypeGuard[JsonObject]:
    return isinstance(value, dict) and all(
        isinstance(key, str) and is_json(item) for key, item in value.items()
    )
