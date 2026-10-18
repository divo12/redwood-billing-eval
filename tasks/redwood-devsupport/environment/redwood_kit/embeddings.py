"""Embeddings for the models the runtime serves.

`embed` is for the build: it calls those models with your `OPENAI_API_KEY`
while you index. The support agent is not given this. If retrieval code you
wrote needs a vector for a new question, that code calls `self.gateway.embed`
with the same model and `dimensions`. The evaluator serves that call. Chunking,
indexing, and search are not provided here.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from openai import OpenAI

from .agent_context import ModelConfig, resolve_call
from .jsontypes import Json, JsonObject

MANIFEST = Path("/app/framework/deployment_manifest.json")
# Provider max strings per request. Each string can be up to the model's own token limit.
PROVIDER_BATCH = 2048

@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(max_retries=5)


def embedding_models(manifest: Path | None = None) -> tuple[ModelConfig, ...]:
    data = json.loads((manifest or MANIFEST).read_text())
    if not isinstance(data, dict):
        raise ValueError("deployment manifest must be an object")
    entries = data.get("allowed_embedding_models", [])
    if not isinstance(entries, list):
        raise ValueError("allowed_embedding_models must be a list")
    return tuple(_model_config(item) for item in entries if isinstance(item, dict))


def _model_config(data: JsonObject) -> ModelConfig:
    model = data.get("model")
    constraints = data.get("constraints", {})
    if not isinstance(model, str) or not isinstance(constraints, dict):
        raise ValueError("invalid embedding model configuration")
    return ModelConfig(model, constraints)


def checked_inputs(inputs: Sequence[str]) -> list[str]:
    ok = (
        isinstance(inputs, Sequence)
        and not isinstance(inputs, str)
        and len(inputs) > 0
        and all(isinstance(text, str) and text.strip() for text in inputs)
    )
    if not ok:
        raise ValueError("inputs must be non-empty strings")
    return list(inputs)


def embed(*, model: str, inputs: Sequence[str], **kwargs: Json) -> list[list[float]]:
    """One vector per input, in order. Provider requests are batched inside this call."""
    texts = checked_inputs(inputs)
    request_args = resolve_call(embedding_models(), model, kwargs)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), PROVIDER_BATCH):
        batch = texts[start:start + PROVIDER_BATCH]
        response = _client().embeddings.create(model=model, input=batch, extra_body=request_args)
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
    return vectors
