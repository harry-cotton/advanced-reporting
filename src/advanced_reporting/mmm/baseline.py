"""Transparent baseline MMM: geometric adstock + Hill saturation + ridge-regularized,
non-negative regression.

Media coefficients are constrained non-negative and L2-regularized (ridge), which
stabilizes attribution when channel spends are correlated — the central difficulty
in MMM. The ridge strength is picked by held-out error. Per-channel uncertainty
comes from a block bootstrap and feeds the guarded commentary layer. Fast and
dependency-light: it proves the pipeline and sanity-checks the heavier Meridian engine.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from .base import BaseMMM, MMMResult
from .transforms import geometric_adstock, hill_saturation


def _r2(y, yhat):
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _mape(y, yhat):
    m = y != 0
    return float(np.mean(np.abs((y[m] - yhat[m]) / y[m]))) if m.any() else float("nan")


class BaselineMMM(BaseMMM):
    name = "baseline"

    DECAY_GRID = tuple(np.round(np.arange(0.0, 0.85, 0.1), 2))   # 0.0 .. 0.8
    HALF_QUANTILES = (0.25, 0.50, 0.75)
    SLOPE_GRID = (0.8, 1.0, 1.2, 1.5)
    CV_FOLDS = 5

    def __init__(self, adstock_max_lag: int = 8, train_frac: float = 0.85,
                 ridge_alphas=(0.05, 0.5, 1.0, 3.0, 10.0, 30.0, 75.0, 200.0, 500.0),
                 n_boot: int = 200, block: int = 8, seed: int = 0):
        self.adstock_max_lag = adstock_max_lag
        self.train_frac = train_frac
        self.ridge_alphas = tuple(ridge_alphas)
        self.n_boot = n_boot
        self.block = block
        self.rng = np.random.default_rng(seed)

    def _features(self, df, channel_cols, control_cols, target, n_train):
        """Build the design. All transform hyperparameters are selected on TRAIN weeks
        only, and against a partial residual (y minus a train-fit of the non-media
        block) rather than raw y — raw y is dominated by smooth trend/seasonality, which
        systematically rewards maximum adstock smoothing (the old behavior: every
        channel's decay saturated at the grid max)."""
        n = len(df)
        t = np.arange(n)
        y = df[target].to_numpy(float)
        nonmedia = {
            "const": np.ones(n), "trend": t / max(n, 1),
            "season_sin": np.sin(2 * np.pi * t / 52.0),
            "season_cos": np.cos(2 * np.pi * t / 52.0),
        }
        for c in control_cols:
            if c in df.columns:
                nonmedia[c] = df[c].to_numpy(float)

        # partial residual: OLS of y on the non-media block, fit on train rows only
        NM = np.column_stack(list(nonmedia.values()))
        beta = np.linalg.lstsq(NM[:n_train], y[:n_train], rcond=None)[0]
        resid = y - NM @ beta

        media, params = {}, {}
        for ch in channel_cols:
            spend = df[ch].to_numpy(float)
            best = None   # (|corr|, decay, half, slope, transformed-full-series)
            for decay in self.DECAY_GRID:
                ad = geometric_adstock(spend, decay, self.adstock_max_lag)
                pos_tr = ad[:n_train][ad[:n_train] > 0]
                if ad[:n_train].std() == 0 or not len(pos_tr):
                    continue
                for q in self.HALF_QUANTILES:
                    half = float(np.quantile(pos_tr, q))
                    if half <= 0:
                        continue
                    for slope in self.SLOPE_GRID:
                        m = hill_saturation(ad, half, slope)
                        if m[:n_train].std() == 0:
                            continue
                        r = abs(np.corrcoef(m[:n_train], resid[:n_train])[0, 1])
                        if best is None or r > best[0]:
                            best = (r, float(decay), half, slope, m)
            if best is None:   # degenerate spend series: fall back to a mild default
                decay, half, slope = 0.3, 1.0, 1.0
                m = hill_saturation(
                    geometric_adstock(spend, decay, self.adstock_max_lag), half, slope)
            else:
                _, decay, half, slope, m = best
            media[ch] = m
            params[ch] = dict(decay=decay, half_sat=half, slope=slope,
                              mean_spend=float(spend.mean()), max_spend=float(spend.max()))
        return nonmedia, media, params, y

    @staticmethod
    def _roi_scale(M, spends):
        """Column scales such that each scaled media column sums to the channel's total
        spend — the fitted coefficient then IS the channel's ROI, and the ridge penalty
        shrinks ROI comparably across channels. (Unit-std scaling made a tiny channel's
        coefficient as cheap as a huge one's, letting small channels absorb unrelated
        variance; this is the poor man's version of a Bayesian ROI prior.)"""
        col = M.sum(axis=0)
        scale = np.where((col > 0) & (spends > 0), col / np.maximum(spends, 1e-9), 1.0)
        return np.where(scale > 0, scale, 1.0)

    def _cv_fold_errs(self, NM, Ms, y, alpha, n_tr):
        """Per-fold rolling-origin CV errors within train: expanding-window fits, each
        validated on the next consecutive chunk of the last ~30% of train. Selection
        never sees the terminal test block."""
        n_first = int(n_tr * 0.7)
        bounds = np.linspace(n_first, n_tr, self.CV_FOLDS + 1).astype(int)
        errs = []
        for lo, hi in zip(bounds[:-1], bounds[1:]):
            if hi <= lo:
                continue
            nmc, mc = self._solve(NM[:lo], Ms[:lo], y[:lo], alpha)
            errs.append(_mape(y[lo:hi], NM[lo:hi] @ nmc + Ms[lo:hi] @ mc))
        return errs

    def _cv_err(self, NM, Ms, y, alpha, n_tr):
        errs = self._cv_fold_errs(NM, Ms, y, alpha, n_tr)
        return float(np.mean(errs)) if errs else float("inf")

    @staticmethod
    def _solve(NM, Ms, y, alpha):
        """Constrained partial-pooling ridge: media coefs >= 0, penalized toward their
        COMMON mean (not zero) — with spend-scaled media columns the coefficients are
        ROIs, so this shrinks each channel's ROI toward the cross-channel level while
        leaving that level free. Outlier ROI claims are expensive; honest ones are not.
        Non-media coefficients are unpenalized."""
        n, p = NM.shape
        k = Ms.shape[1]
        A = np.hstack([NM, Ms])
        b = y
        if alpha > 0 and k > 1:
            # rows penalize sqrt(alpha) * (coef_j - mean(coef)): deviation from the pool
            pool = np.sqrt(alpha) * y.std() * (np.eye(k) - np.ones((k, k)) / k)
            P = np.hstack([np.zeros((k, p)), pool])
            A = np.vstack([A, P])
            b = np.concatenate([y, np.zeros(k)])
        lb = np.array([-np.inf] * p + [0.0] * k)
        ub = np.array([np.inf] * (p + k))
        c = lsq_linear(A, b, bounds=(lb, ub), max_iter=300).x
        return c[:p], c[p:]

    def fit(self, model_df, channel_cols, control_cols, target="revenue", date_col="date"):
        df = model_df.reset_index(drop=True)
        n = len(df)
        n_tr = int(n * self.train_frac)
        # transforms selected on train weeks only (test block never touches selection)
        nonmedia, media, params, y = self._features(df, channel_cols, control_cols,
                                                    target, n_tr)
        nm_names, m_names = list(nonmedia), list(media)
        NM = np.column_stack([nonmedia[k] for k in nm_names])
        M = np.column_stack([media[k] for k in m_names])

        # Coordinate refinement: the univariate init can't disambiguate correlated
        # channels (one channel absorbs the shared signal, non-negativity zeroes the
        # rest). Re-pick each channel's (decay, half_sat, slope) by the FULL regression's
        # rolling-origin CV error within train, holding the other channels' transforms
        # fixed. The terminal test block never touches selection.
        spend_arr = {ch: df[ch].to_numpy(float) for ch in m_names}
        spends = np.array([spend_arr[ch].sum() for ch in m_names])
        alpha0 = 1.0
        for _sweep in range(2):
            for j, ch in enumerate(m_names):
                best = None
                for decay in self.DECAY_GRID:
                    ad = geometric_adstock(spend_arr[ch], decay, self.adstock_max_lag)
                    pos_tr = ad[:n_tr][ad[:n_tr] > 0]
                    if ad[:n_tr].std() == 0 or not len(pos_tr):
                        continue
                    for q in self.HALF_QUANTILES:
                        half = float(np.quantile(pos_tr, q))
                        if half <= 0:
                            continue
                        for slope in self.SLOPE_GRID:
                            m = hill_saturation(ad, half, slope)
                            if m[:n_tr].std() == 0:
                                continue
                            Mtry = M.copy()
                            Mtry[:, j] = m
                            err = self._cv_err(NM, Mtry / self._roi_scale(Mtry, spends),
                                               y, alpha0, n_tr)
                            if best is None or err < best[0]:
                                best = (err, float(decay), half, float(slope), m)
                if best is not None:
                    _, decay, half, slope, m = best
                    M[:, j] = m
                    media[ch] = m
                    params[ch].update(decay=decay, half_sat=half, slope=slope)

        sd = self._roi_scale(M, spends)
        Ms = M / sd  # spend-scaled: coefficients are ROI-like, penalized comparably

        # pick pooling strength by the same rolling-origin CV within train (the terminal
        # test block stays untouched by every selection step), using the 1-SE rule:
        # take the LARGEST alpha within one standard error of the best CV error — when
        # the data can't distinguish pooling strengths, prefer more pooling (stability)
        # over less (variance). This is what keeps small channels from claiming outsized
        # ROI on noisy fits.
        cv = {a: self._cv_fold_errs(NM, Ms, y, a, n_tr) for a in self.ridge_alphas}
        means = {a: float(np.mean(e)) if e else float("inf") for a, e in cv.items()}
        a_best = min(means, key=means.get)
        se = (float(np.std(cv[a_best], ddof=1) / np.sqrt(len(cv[a_best])))
              if len(cv[a_best]) > 1 else 0.0)
        cutoff = means[a_best] + se
        alpha = max((a for a in self.ridge_alphas if means[a] <= cutoff),
                    default=a_best)

        nm_coef, mc_s = self._solve(NM, Ms, y, alpha)
        pred = NM @ nm_coef + Ms @ mc_s
        mc_raw = mc_s / sd  # back to per-unit-spend-feature scale (for response curves)

        # per-week contributions
        contrib = pd.DataFrame({date_col: df[date_col].values})
        for j, ch in enumerate(m_names):
            contrib[ch] = mc_s[j] * Ms[:, j]
        contrib["baseline"] = NM @ nm_coef

        # honest holdout: transforms and alpha were selected without these weeks, and
        # they are evaluated here exactly once
        nmc_tr, mc_tr = self._solve(NM[:n_tr], Ms[:n_tr], y[:n_tr], alpha)
        pred_te = NM[n_tr:] @ nmc_tr + Ms[n_tr:] @ mc_tr
        fit_metrics = dict(r2=_r2(y, pred), mape=_mape(y, pred),
                           test_r2=_r2(y[n_tr:], pred_te), test_mape=_mape(y[n_tr:], pred_te),
                           n_obs=n, n_train=n_tr, ridge_alpha=float(alpha))

        # bootstrap CIs (block resample of weeks) at the chosen alpha
        boot = {ch: {"contrib": [], "roi": []} for ch in m_names}
        L = self.block
        n_blocks = int(np.ceil(n / L))
        spend_tot = {ch: float(df[ch].sum()) for ch in m_names}
        col_sum = Ms.sum(axis=0)
        for _ in range(self.n_boot):
            starts = self.rng.integers(0, max(n - L + 1, 1), size=n_blocks)
            idx = np.concatenate([np.arange(s, min(s + L, n)) for s in starts])[:n]
            _, mcb = self._solve(NM[idx], Ms[idx], y[idx], alpha)
            for j, ch in enumerate(m_names):
                c_tot = mcb[j] * col_sum[j]
                boot[ch]["contrib"].append(c_tot)
                boot[ch]["roi"].append(c_tot / spend_tot[ch] if spend_tot[ch] > 0 else np.nan)

        total_pred = float(pred.sum())
        rows = []
        for j, ch in enumerate(m_names):
            c_tot = float(contrib[ch].sum())
            roi = c_tot / spend_tot[ch] if spend_tot[ch] > 0 else np.nan
            rows.append(dict(
                channel=ch, spend=spend_tot[ch], contribution=c_tot,
                contribution_share=c_tot / total_pred if total_pred else np.nan, roi=roi,
                contribution_low=float(np.percentile(boot[ch]["contrib"], 5)),
                contribution_high=float(np.percentile(boot[ch]["contrib"], 95)),
                roi_low=float(np.nanpercentile(boot[ch]["roi"], 5)),
                roi_high=float(np.nanpercentile(boot[ch]["roi"], 95)),
                adstock_decay=params[ch]["decay"], half_sat=params[ch]["half_sat"]))
        summary = pd.DataFrame(rows).sort_values("contribution", ascending=False).reset_index(drop=True)

        curves = {}
        for j, ch in enumerate(m_names):
            p = params[ch]
            grid = np.linspace(0, p["max_spend"] * 1.5, 60)
            curves[ch] = dict(spend=grid,
                              response=mc_raw[j] * hill_saturation(grid, p["half_sat"], p["slope"]),
                              mean_spend=p["mean_spend"])

        return MMMResult(engine=self.name, contributions=contrib, channel_summary=summary,
                         fit_metrics=fit_metrics, response_curves=curves,
                         predicted=pd.Series(pred), actual=pd.Series(y),
                         dates=pd.to_datetime(df[date_col]), params=params)
