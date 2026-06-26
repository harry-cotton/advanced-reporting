"""MMM engine interface and the result object every engine returns."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class MMMResult:
    engine: str
    contributions: pd.DataFrame    # per-week contribution by channel + 'baseline'
    channel_summary: pd.DataFrame  # per channel: spend, contribution, roi (+ CIs), share
    fit_metrics: dict              # r2, mape, holdout r2/mape
    response_curves: dict          # channel -> {spend, response, mean_spend}
    predicted: pd.Series
    actual: pd.Series
    dates: pd.Series
    params: dict = field(default_factory=dict)


class BaseMMM(ABC):
    name: str = "base"

    @abstractmethod
    def fit(self, model_df: pd.DataFrame, channel_cols, control_cols,
            target: str = "revenue", date_col: str = "date") -> MMMResult:
        raise NotImplementedError
