"""The one guarded LLM gateway — every model call in the project goes through here.

Design rules (2026-07 review):
- Deterministic paths never import ``anthropic`` (lazy import here; the SDK is an
  optional extra: ``uv sync --extra llm`` / ``pip install -e .[llm]``).
- Structured outputs (``output_config`` json_schema) instead of free-text JSON
  scraping — a schema-conforming reply is guaranteed, so "billed but unparseable"
  cannot happen.
- Every call returns ``(data, info)`` with token usage and dollar cost; failures are
  logged once at WARNING and returned as ``(None, info)`` — never silently swallowed,
  never fatal (callers fall back to their deterministic paths).
- ``.env`` at the project root is honored (the documented setup: the key used to work
  only via the real environment because nothing on the LLM paths loaded the file).
- A provider swap (Bedrock / Vertex / Claude Platform on AWS) happens in ``_client()``
  and nowhere else.
"""
from __future__ import annotations

import json
import logging
import os

from .utils import load_env_file

log = logging.getLogger("advanced_reporting.llm")

# USD per 1M tokens (input, output). Retrieved 2026-07-02 from the Anthropic pricing
# table; keyed by model-id prefix so dated snapshots match. An unknown model logs a
# warning and traces cost as None rather than silently reporting wrong dollars.
PRICING = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
}

_env_loaded = False


def _ensure_env() -> None:
    global _env_loaded
    if not _env_loaded:
        load_env_file()          # no-op if absent; real environment always wins
        _env_loaded = True


def llm_enabled() -> bool:
    """True when an API key is available (checks the environment AND project .env)."""
    _ensure_env()
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Dollar cost for a call, or None (with a warning) for an unpriced model."""
    for prefix, (pin, pout) in PRICING.items():
        if model.startswith(prefix):
            return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
    log.warning("no pricing entry for model %r — cost will be traced as None", model)
    return None


def call(prompt: str, *, model: str, schema: dict | None = None, system: str | None = None,
         max_tokens: int = 1024, timeout: float = 30.0):
    """One LLM call. Returns ``(data, info)``.

    ``data`` is a parsed dict when ``schema`` is given (schema-enforced by the API),
    the raw text otherwise, and ``None`` on any failure. ``info`` always carries
    ``model / input_tokens / output_tokens / cost_usd / error``.
    """
    info = {"model": model, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "error": None}
    _ensure_env()
    if not os.getenv("ANTHROPIC_API_KEY"):
        info["error"] = "no ANTHROPIC_API_KEY (environment or project .env)"
        log.warning("LLM call skipped: %s", info["error"])
        return None, info
    try:
        import anthropic  # lazy: optional dependency, deterministic paths never need it
    except ImportError:
        info["error"] = "anthropic SDK not installed (uv sync --extra llm)"
        log.warning("LLM call skipped: %s", info["error"])
        return None, info

    kwargs: dict = {
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if schema is not None:
        # via extra_body so any reasonably recent SDK forwards it; the API enforces
        # the schema server-side and the reply is guaranteed-valid JSON
        kwargs["extra_body"] = {
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    try:
        client = anthropic.Anthropic(timeout=timeout)
        msg = client.messages.create(**kwargs)
        text = next(b.text for b in msg.content if b.type == "text")
        info["input_tokens"] = int(getattr(msg.usage, "input_tokens", 0) or 0)
        info["output_tokens"] = int(getattr(msg.usage, "output_tokens", 0) or 0)
        info["cost_usd"] = cost_usd(model, info["input_tokens"], info["output_tokens"])
        data = json.loads(text) if schema is not None else text
        return data, info
    except Exception as e:  # transport/auth/rate-limit/refusal — fall back, but say so
        info["error"] = f"{type(e).__name__}: {e}"
        log.warning("LLM call failed (%s) — falling back to the deterministic path",
                    info["error"])
        return None, info
