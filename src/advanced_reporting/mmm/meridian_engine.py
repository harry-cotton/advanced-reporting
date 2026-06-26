"""Google Meridian engine adapter (the target MMM engine).

Meridian (https://github.com/google/meridian) is an OPEN-SOURCE Bayesian MMM
library built on TensorFlow Probability — Apache-2.0, `pip install google-meridian`.
It is NOT a hosted Google Cloud service: it's a Python package that runs anywhere
Python + TF run (local, AWS EC2/SageMaker, GCP, Azure). GPU/TPU just speeds up
sampling; CPU works for datasets this size.

This adapter maps our tidy weekly modeling table into Meridian's InputData, fits
the model, and returns the same `MMMResult` shape as the baseline engine, so the
reporting / commentary / dashboard layers are unchanged when you switch engines.

Heads-up: Meridian's API moves between releases. The fit() body follows the
documented national-level workflow; the posterior->MMMResult mapping is guarded so
no half-mapped numbers leak downstream. Validate against your installed version
the first time you switch `modeling.engine` to `meridian`.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from .base import BaseMMM, MMMResult


class MeridianMMM(BaseMMM):
    name = "meridian"

    def __init__(self, n_chains: int = 4, n_adapt: int = 500,
                 n_burnin: int = 500, n_keep: int = 1000, seed: int = 0):
        self.cfg = dict(n_chains=n_chains, n_adapt=n_adapt,
                        n_burnin=n_burnin, n_keep=n_keep, seed=seed)

    @staticmethod
    def _require():
        try:
            import meridian  # noqa: F401
        except Exception as e:  # pragma: no cover - depends on optional heavy dep
            raise ImportError(
                "google-meridian is not installed. `pip install google-meridian` "
                "(pulls TensorFlow Probability), or keep modeling.engine: baseline."
            ) from e

    def _to_long(self, df, channel_cols, control_cols, target, date_col):
        """Reshape the wide weekly modeling table into Meridian's expected long frame.

        Meridian (national) expects, per time period, a KPI plus per-channel media
        execution (impressions/clicks or spend) and media spend, optionally controls.
        Here we use spend as both the media execution metric and the spend metric.
        """
        long_rows = []
        for _, r in df.iterrows():
            for ch in channel_cols:
                long_rows.append({
                    "time": pd.to_datetime(r[date_col]).date().isoformat(),
                    "geo": "national",
                    "channel": ch,
                    "media": float(r[ch]),         # execution proxy (spend)
                    "media_spend": float(r[ch]),   # cost
                })
        media_long = pd.DataFrame(long_rows)
        kpi_cols = [date_col, target] + [c for c in control_cols if c in df.columns]
        kpi = df[kpi_cols].rename(columns={date_col: "time", target: "kpi"})
        kpi["time"] = pd.to_datetime(kpi["time"]).dt.date.astype(str)
        kpi["geo"] = "national"
        return media_long, kpi

    def fit(self, model_df, channel_cols, control_cols, target="revenue", date_col="date") -> MMMResult:
        self._require()
        media_long, kpi = self._to_long(model_df, channel_cols, control_cols, target, date_col)

        try:
            # --- version-sensitive Meridian workflow (validate against your install) ---
            from meridian.data import load
            from meridian.model import model as mmodel
            from meridian.model import spec as mspec
            from meridian.analysis.analyzer import Analyzer

            loader = load.DataFrameDataLoader(
                df=media_long.merge(kpi, on=["time", "geo"], how="left"),
                kpi_type="revenue",
                coord_to_columns=load.CoordToColumns(
                    time="time", geo="geo", kpi="kpi",
                    media=["media"], media_spend=["media_spend"],
                    controls=[c for c in control_cols if c in model_df.columns],
                ),
                media_to_channel={"media": "channel"},
                media_spend_to_channel={"media_spend": "channel"},
            )
            input_data = loader.load()
            model = mmodel.Meridian(input_data=input_data, model_spec=mspec.ModelSpec())
            model.sample_prior(self.cfg["n_keep"])
            model.sample_posterior(n_chains=self.cfg["n_chains"], n_adapt=self.cfg["n_adapt"],
                                   n_burnin=self.cfg["n_burnin"], n_keep=self.cfg["n_keep"])
            analyzer = Analyzer(model)  # noqa: F841  (used in mapping below)
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                f"Meridian fit failed for the installed version ({type(e).__name__}: {e}). "
                "Adjust the loader/model calls above to match your Meridian release, then "
                "complete the Analyzer->MMMResult mapping below."
            ) from e

        # --- Analyzer -> MMMResult mapping: finish for your Meridian version ---
        # Pull incremental_outcome()/roi()/response_curves() off `analyzer`, reshape to:
        #   contributions (per week per channel + 'baseline'), channel_summary (with
        #   posterior credible intervals as *_low/*_high), response_curves, fit_metrics.
        raise NotImplementedError(
            "Meridian fit/sample wired; complete the Analyzer->MMMResult mapping for your "
            "installed version (incremental_outcome / roi / response_curves). Until then the "
            "pipeline default `baseline` produces the same MMMResult shape end-to-end."
        )
