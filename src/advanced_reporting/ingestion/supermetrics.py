"""Supermetrics ingestion — Phase 2 stub.

When wired, this pulls granular spend/impression/conversion data across
Google / Meta / TikTok / LinkedIn through one Supermetrics query and returns the
SAME long schema as CSVSource, so nothing downstream changes.

Auth: set SUPERMETRICS_API_KEY in .env (never commit it).
"""
from __future__ import annotations
import os
from .base import DataSource
from . import schema

# Single-sourced from the canonical schema so the connector and the rest of the
# pipeline can never drift apart.
REQUIRED_SCHEMA = list(schema.REQUIRED_COLUMNS)


class SupermetricsSource(DataSource):
    name = "supermetrics"

    def __init__(self, data_source=None, fields=None, start=None, end=None):
        self.api_key = os.getenv("SUPERMETRICS_API_KEY")
        self.data_source = data_source
        self.fields = fields or REQUIRED_SCHEMA
        self.start, self.end = start, end

    def fetch(self, start: str | None = None, end: str | None = None):
        raise NotImplementedError(
            "SupermetricsSource is a Phase-2 stub. Wire it via the Supermetrics "
            "connector and return columns: " + ", ".join(REQUIRED_SCHEMA)
        )
