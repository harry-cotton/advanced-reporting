"""The agent system (AGENT_SYSTEM_BRIEF.md): agents CONFIGURE, the deterministic
engine COMPUTES, agents NARRATE over computed facts.

A1 (this package): the report-spec agent — one guarded structured-output call at
pipeline time that writes ``outputs/report_spec.json``. The dashboard reads the spec
exactly as it reads config (spec fills the gaps config leaves; explicit config keys
always win). No key / no spec -> current behavior, unchanged.
"""
from .spec_agent import generate_spec, load_active_spec  # noqa: F401
