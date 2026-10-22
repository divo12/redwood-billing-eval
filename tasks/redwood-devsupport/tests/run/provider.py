"""Failures of a model provider, shared by the gateways."""


class ProviderError(RuntimeError):
    """The model provider failed (credentials, billing, limits, outage): an invalid run, never a score."""
