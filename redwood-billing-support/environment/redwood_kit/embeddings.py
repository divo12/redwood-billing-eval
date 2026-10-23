"""Embeddings for the models the runtime serves: local sentence-transformer models, no API.

`embed` is for the build: it runs the model locally while you index. The support agent is not
given the model. If retrieval code you wrote needs a vector for a new question, that code calls
`self.gateway.embed` with the same model; the evaluator serves that call with the same weights,
so build-time and runtime vectors match. Chunking, indexing, and search are not provided here.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding

from .agent_context import ModelConfig, resolve_call
from .jsontypes import Json, JsonObject

MANIFEST = Path("/app/framework/deployment_manifest.json")
MODEL_CACHE = Path("/opt/redwood-models")  # weights baked into the image; nothing is downloaded at runtime
BATCH = 32
THREADS = 2  # the container's CPU quota; more threads only contend for it
_LOCK = threading.Lock()  # one model per process, shared by the verifier's conversation threads


@lru_cache(maxsize=4)
def _model(name: str) -> TextEmbedding:
    return TextEmbedding(model_name=name, cache_dir=str(MODEL_CACHE), local_files_only=True, threads=THREADS)


def encode(model: str, texts: list[str]) -> list[list[float]]:
    """Vectors for `texts`, in order, from a locally stored model."""
    with _LOCK:
        return [vector.tolist() for vector in _model(model).embed(texts, batch_size=BATCH)]


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
    """One vector per input, in order. The model runs locally; pass all texts in one call."""
    texts = checked_inputs(inputs)
    if kwargs:
        raise ValueError(f"unsupported embedding arguments: {sorted(kwargs)}")
    resolve_call(embedding_models(), model, kwargs)  # the model must be in the manifest
    return encode(model, texts)
