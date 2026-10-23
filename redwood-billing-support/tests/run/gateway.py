"""Grading's model gateway: the shared one in redwood_kit (also used by the practice service), re-exported."""

import litellm
from litellm.exceptions import BadRequestError, InternalServerError, RateLimitError
from redwood_kit.gateway import (
    ALLOWED_KWARGS,
    CACHE_POINTS,
    DEFAULT_MODEL,
    EFFORT,
    EMBED_KWARGS,
    EMBEDDING_MODELS,
    MODELS,
    OPENING,
    SUPPORT_MODEL,
    USER_MODEL,
    ProviderError,
    complete,
    embed,
    generate,
    json_list,
)

__all__ = ["ALLOWED_KWARGS", "CACHE_POINTS", "DEFAULT_MODEL", "EFFORT", "EMBEDDING_MODELS", "EMBED_KWARGS", "MODELS",
           "OPENING", "SUPPORT_MODEL", "USER_MODEL", "BadRequestError", "InternalServerError", "ProviderError",
           "RateLimitError", "complete", "embed", "generate", "json_list", "litellm"]
