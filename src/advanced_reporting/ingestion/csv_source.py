"""Read campaign / KPI data from local CSV files."""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from .base import DataSource
from . import schema
from ..utils import load_mappings


class CSVSource(DataSource):
    def __init__(self, path, name: str = "csv", source: str | None = None,
                 apply_schema: bool = True):
        self.path = Path(path)
        self.name = name
        self.source = source or "default"   # which sources: map in mappings.yaml to use
        self.apply_schema = apply_schema

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        if not self.path.exists():
            raise FileNotFoundError(
                f"{self.path} not found — run `python scripts/generate_sample_data.py` first."
            )
        df = pd.read_csv(self.path)
        if self.apply_schema:
            try:
                df = schema.to_canonical(df, self.source, load_mappings())
            except schema.SchemaError:
                # Non-ad files (e.g. the business KPI table) don't have the canonical ad
                # columns; pass them through untouched rather than forcing the ad schema.
                pass
        return self._filter_dates(df, start, end)

    @staticmethod
    def _filter_dates(df: pd.DataFrame, start, end) -> pd.DataFrame:
        """Inclusive [start, end] filter on the ``date`` column (no-op if both None)."""
        if (start is None and end is None) or "date" not in df.columns:
            return df
        d = pd.to_datetime(df["date"], errors="coerce")
        mask = pd.Series(True, index=df.index)
        if start is not None:
            mask &= d >= pd.Timestamp(start)
        if end is not None:
            mask &= d <= pd.Timestamp(end)
        return df[mask].reset_index(drop=True)
