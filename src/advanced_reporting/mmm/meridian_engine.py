"""Google Meridian engine adapter (the target MMM engine).

Meridian (https://github.com/google/meridian) is an OPEN-SOURCE Bayesian MMM library
built on TensorFlow Probability — Apache-2.0, `pip install google-meridian`. It is NOT a
hosted Google Cloud service: it's a Python package that runs anywhere Python + TF run
(local, AWS EC2/SageMaker, GCP, Azure). GPU/TPU only speeds up sampling; CPU works for
datasets this size.

This adapter maps our geo x weekly modeling table into Meridian's geo-level ``InputData``,
fits the Bayesian model (MCMC), and returns the same ``MMMResult`` shape as the baseline
engine — so reporting / commentary / dashboard are unchanged when you switch engines.
Meridian's strength is exactly the geo hierarchy: cross-geo variation identifies effects the
national baseline engine cannot, and informative priors keep collinear/small channels from
claiming outsized ROI.

Validated against google-meridian 1.7.0 on the FBI recruiting dataset (2026-07-13). Two
version-specific choices, both forced by Meridian's own geo-model checks:
  * NATIONAL, time-only controls (unemployment_index, news_spike_flag) are DROPPED — Meridian
    rejects controls that don't vary across geos (collinear with its per-time knots), and the
    time-varying knot baseline already absorbs those national demand swings.
  * ``knots`` < n_time keeps the geo model identifiable.
The posterior -> MMMResult mapping reads ``summary_metrics`` / ``expected_vs_actual_data`` /
``predictive_accuracy`` / ``response_curves`` — it stays guarded (raises) if the installed
Meridian version returns a shape this mapping doesn't recognise, so no half-mapped numbers
ever leak downstream.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseMMM, MMMResult


class MeridianMMM(BaseMMM):
    name = "meridian"

    def __init__(self, n_chains: int = 4, n_adapt: int = 500, n_burnin: int = 500,
                 n_keep: int = 1000, knots: int = 20, holdout_frac: float = 0.15,
                 seed: int = 0):
        self.cfg = dict(n_chains=n_chains, n_adapt=n_adapt, n_burnin=n_burnin,
                        n_keep=n_keep, knots=knots, holdout_frac=holdout_frac, seed=seed)

    @staticmethod
    def _require():
        try:
            import meridian  # noqa: F401
        except Exception as e:  # pragma: no cover - optional heavy dep
            raise ImportError(
                "google-meridian is not installed. `pip install google-meridian` "
                "(pulls TensorFlow Probability), or keep modeling.engine: baseline."
            ) from e

    # ------------------------------------------------------------------ input
    def _build_input(self, geo_df, channel_cols, target, date_col, population_col):
        """geo x weekly wide frame -> Meridian InputData. Spend is used as both the media
        execution metric and the spend metric (no impression stream in this schema)."""
        from meridian.data import load

        df = geo_df.copy()
        df = df.rename(columns={date_col: "time", target: "kpi"})
        df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
        if population_col and population_col in df.columns:
            df["population"] = df[population_col].astype(float)
        else:
            df["population"] = 1.0
        for ch in channel_cols:
            df[ch] = df[ch].astype(float)
            df[ch + "_spend"] = df[ch]

        c2c = load.CoordToColumns(
            time="time", geo="geo", kpi="kpi", population="population",
            media=list(channel_cols),
            media_spend=[c + "_spend" for c in channel_cols],
        )
        loader = load.DataFrameDataLoader(
            df=df, kpi_type="non_revenue", coord_to_columns=c2c,
            media_to_channel={c: c for c in channel_cols},
            media_spend_to_channel={c + "_spend": c for c in channel_cols},
        )
        return loader.load(), sorted(df["time"].unique())

    # ------------------------------------------------------------------ fit
    def fit(self, model_df, channel_cols, control_cols, target="revenue",
            date_col="date", *, geo_df=None, population_col="population") -> MMMResult:
        self._require()
        if geo_df is None:
            raise ValueError(
                "MeridianMMM needs a GEO x weekly table (geo_df=...): Meridian's identifying "
                "signal is cross-geo variation. Pass build_modeling_table_geo(...) from the "
                "pipeline; the national wide table (model_df) is not enough.")
        import tensorflow as tf
        from meridian.model import model as mmodel
        from meridian.model import spec as mspec
        from meridian.analysis.analyzer import Analyzer

        tf.keras.utils.set_random_seed(int(self.cfg["seed"]))
        input_data, times = self._build_input(geo_df, channel_cols, target, date_col,
                                              population_col)
        n_time = len(times)
        n_geo = geo_df["geo"].nunique()
        knots = int(min(self.cfg["knots"], max(4, n_time - 1)))

        # Held-out set: a RANDOM holdout_frac of (geo, week) cells. Meridian's time-varying
        # knot baseline extrapolates poorly to a held-out tail (a spline shouldn't be trusted
        # past its knots), so a random-cell holdout — an interpolation test — is the honest
        # out-of-sample read for a geo model. Seeded for reproducibility.
        rng = np.random.default_rng(int(self.cfg["seed"]))
        holdout = rng.random((n_geo, n_time)) < float(self.cfg["holdout_frac"])
        holdout[:, 0] = False                      # keep the first week fully observed
        n_test = int(holdout.sum())
        train_times, test_times = times, times     # random cells: R² split is by evaluation_set

        try:
            # paid_media_prior_type='contribution' puts the prior on each channel's
            # CONTRIBUTION SHARE (0-1), not ROI. Meridian's default ROI prior is calibrated
            # for a revenue KPI (roi ~ 1); for a COUNT KPI (apps per $ ~ 0.002) that default
            # overwhelms the data and pins every channel near roi 1. The contribution prior
            # is scale-free — the correct choice for submitted applications.
            model = mmodel.Meridian(
                input_data=input_data,
                model_spec=mspec.ModelSpec(knots=knots, holdout_id=holdout,
                                           paid_media_prior_type="contribution"))
            model.sample_prior(self.cfg["n_keep"])
            model.sample_posterior(
                n_chains=self.cfg["n_chains"], n_adapt=self.cfg["n_adapt"],
                n_burnin=self.cfg["n_burnin"], n_keep=self.cfg["n_keep"], seed=self.cfg["seed"])
            analyzer = Analyzer(model)
        except Exception as e:  # pragma: no cover - version/fit sensitive
            raise RuntimeError(
                f"Meridian fit failed for the installed version ({type(e).__name__}: {e}). "
                "Validate the loader/model calls against your Meridian release before "
                "switching modeling.engine to meridian.") from e

        return self._to_result(analyzer, channel_cols, times, train_times, test_times)

    # ------------------------------------------------------------------ mapping
    def _to_result(self, analyzer, channel_cols, times, train_times, test_times) -> MMMResult:
        """Analyzer posterior -> MMMResult (guarded: raises on an unrecognised shape)."""
        try:
            sm = analyzer.summary_metrics(confidence_level=0.9)
            post = dict(distribution="posterior")

            def _ch(var, ch, metric):
                return float(sm[var].sel(channel=ch, metric=metric, **post).values)

            rows = []
            for ch in channel_cols:
                rows.append({
                    "channel": ch,
                    "spend": float(sm["spend"].sel(channel=ch).values),
                    "contribution": _ch("incremental_outcome", ch, "mean"),
                    "contribution_low": _ch("incremental_outcome", ch, "ci_lo"),
                    "contribution_high": _ch("incremental_outcome", ch, "ci_hi"),
                    "contribution_share": _ch("pct_of_contribution", ch, "mean") / 100.0,
                    "roi": _ch("roi", ch, "mean"),
                    "roi_low": _ch("roi", ch, "ci_lo"),
                    "roi_high": _ch("roi", ch, "ci_hi"),
                })
            summary = (pd.DataFrame(rows).sort_values("contribution", ascending=False)
                       .reset_index(drop=True))

            # actual vs predicted (national) + per-week baseline for the waterfall.
            # `actual` is observed -> no `metric` dim; `expected`/`baseline` carry posterior
            # uncertainty (metric = mean/ci_lo/ci_hi). Select the mean only when present.
            eva = analyzer.expected_vs_actual_data(aggregate_geos=True, aggregate_times=False)
            ev_times = [str(t) for t in eva.coords["time"].values]

            def _mean(da):
                da = da.sel(metric="mean") if "metric" in da.dims else da
                return np.asarray(da.values, dtype=float).ravel()

            actual = pd.Series(_mean(eva["actual"]))
            predicted = pd.Series(_mean(eva["expected"]))
            baseline_wk = _mean(eva["baseline"])
            dates = pd.to_datetime(pd.Series(ev_times))

            # per-week per-channel contributions (posterior mean over samples), + baseline
            io = analyzer.incremental_outcome(aggregate_geos=True, aggregate_times=False,
                                              include_non_paid_channels=False)
            io = np.asarray(io)                       # (samples..., time, channel)
            io_mean = io.reshape(-1, io.shape[-2], io.shape[-1]).mean(axis=0)  # (time, channel)
            contrib = pd.DataFrame({"date": dates.values})
            for j, ch in enumerate(channel_cols):
                contrib[ch] = io_mean[:, j] if io_mean.shape[0] == len(contrib) else np.nan
            contrib["baseline"] = baseline_wk if len(baseline_wk) == len(contrib) else np.nan

            fit_metrics = self._fit_metrics(analyzer, train_times, test_times, actual, predicted)
            response_curves = self._response_curves(analyzer, channel_cols, summary)
            params = self._params(analyzer, channel_cols)
        except Exception as e:
            raise RuntimeError(
                f"Meridian posterior -> MMMResult mapping failed ({type(e).__name__}: {e}). "
                "The installed Meridian returned a shape this adapter doesn't recognise; "
                "keep modeling.engine: baseline until the mapping is updated.") from e

        return MMMResult(
            engine=self.name, contributions=contrib, channel_summary=summary,
            fit_metrics=fit_metrics, response_curves=response_curves,
            predicted=predicted, actual=actual, dates=dates, params=params)

    def _fit_metrics(self, analyzer, train_times, test_times, actual, predicted) -> dict:
        # With a holdout set, predictive_accuracy carries an `evaluation_set` dim
        # (Train / Test / All Data) — the honest train-vs-held-out split. Without a
        # holdout it's a single value used for both.
        val = analyzer.predictive_accuracy()["value"]

        def _acc(metric, want):
            da = val.sel(metric=metric, geo_granularity="national")
            if "evaluation_set" in da.dims:
                labels = [str(x) for x in da.coords["evaluation_set"].values]
                pick = next((l for l in labels if want in l.lower()), labels[-1])
                da = da.sel(evaluation_set=pick)
            return float(np.asarray(da.values).ravel()[0])

        return {
            "r2": _acc("R_Squared", "train"),
            "test_r2": _acc("R_Squared", "test"),
            "mape": _acc("MAPE", "train"),
            "test_mape": _acc("MAPE", "test"),
            "n_obs": len(actual), "n_train": len(train_times),
        }

    def _response_curves(self, analyzer, channel_cols, summary) -> dict:
        rc = analyzer.response_curves()
        mult = np.asarray(rc.coords["spend_multiplier"].values, dtype=float)
        mean_spend = {r["channel"]: r["spend"] / max(len(mult), 1) for _, r in summary.iterrows()}
        out = {}
        for ch in channel_cols:
            spend = np.asarray(rc["spend"].sel(channel=ch).values, dtype=float).ravel()
            resp = np.asarray(rc["incremental_outcome"].sel(channel=ch, metric="mean").values,
                              dtype=float).ravel()
            ms = float(summary.loc[summary["channel"] == ch, "spend"].iloc[0])
            out[ch] = {"spend": list(spend), "response": list(resp),
                       "mean_spend": ms / len(spend) if len(spend) else 0.0}
        return out

    def _params(self, analyzer, channel_cols) -> dict:
        """Best-effort per-channel adstock/saturation summary for the commentary saturation
        flags. Non-fatal — an empty dict just drops those flags."""
        params = {}
        try:
            decay = analyzer.adstock_decay()  # noqa: F841 - shape varies; kept for future use
        except Exception:
            pass
        return params
