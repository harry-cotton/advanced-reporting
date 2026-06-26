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

    def __init__(self, adstock_max_lag: int = 8, train_frac: float = 0.85,
                 ridge_alphas=(0.5, 1.0, 3.0, 10.0, 30.0, 75.0),
                 n_boot: int = 200, block: int = 8, seed: int = 0):
        self.adstock_max_lag = adstock_max_lag
        self.train_frac = train_frac
        self.ridge_alphas = tuple(ridge_alphas)
        self.n_boot = n_boot
        self.block = block
        self.rng = np.random.default_rng(seed)

    def _features(self, df, channel_cols, control_cols, target):
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
        media, params = {}, {}
        for ch in channel_cols:
            spend = df[ch].to_numpy(float)
            best = None
            for decay in np.round(np.arange(0.0, 0.75, 0.1), 2):
                ad = geometric_adstock(spend, decay, self.adstock_max_lag)
                if ad.std() == 0:
                    continue
                r = abs(np.corrcoef(ad, y)[0, 1])
                if best is None or r > best[0]:
                    best = (r, decay, ad)
            decay = best[1] if best else 0.3
            ad = best[2] if best else geometric_adstock(spend, decay, self.adstock_max_lag)
            pos = ad[ad > 0]
            half = float(np.median(pos)) if len(pos) else 1.0
            media[ch] = hill_saturation(ad, half, 1.0)
            params[ch] = dict(decay=float(decay), half_sat=half, slope=1.0,
                              mean_spend=float(spend.mean()), max_spend=float(spend.max()))
        return nonmedia, media, params, y

    @staticmethod
    def _solve(NM, Ms, y, alpha):
        """Constrained ridge: media coefs >= 0 and L2-penalized; non-media free."""
        n, p = NM.shape
        k = Ms.shape[1]
        A = np.hstack([NM, Ms])
        b = y
        if alpha > 0:
            P = np.hstack([np.zeros((k, p)), np.sqrt(alpha) * np.eye(k)])
            A = np.vstack([A, P])
            b = np.concatenate([y, np.zeros(k)])
        lb = np.array([-np.inf] * p + [0.0] * k)
        ub = np.array([np.inf] * (p + k))
        c = lsq_linear(A, b, bounds=(lb, ub), max_iter=300).x
        return c[:p], c[p:]

    def fit(self, model_df, channel_cols, control_cols, target="revenue", date_col="date"):
        df = model_df.reset_index(drop=True)
        nonmedia, media, params, y = self._features(df, channel_cols, control_cols, target)
        nm_names, m_names = list(nonmedia), list(media)
        NM = np.column_stack([nonmedia[k] for k in nm_names])
        M = np.column_stack([media[k] for k in m_names])
        sd = M.std(axis=0)
        sd[sd == 0] = 1.0
        Ms = M / sd  # scale media to unit std so ridge penalizes them comparably
        n = len(df)
        n_tr = int(n * self.train_frac)

        # pick ridge strength by held-out MAPE
        best = None
        for alpha in self.ridge_alphas:
            nmc, mc = self._solve(NM[:n_tr], Ms[:n_tr], y[:n_tr], alpha)
            err = _mape(y[n_tr:], NM[n_tr:] @ nmc + Ms[n_tr:] @ mc)
            if best is None or err < best[0]:
                best = (err, alpha)
        alpha = best[1]

        nm_coef, mc_s = self._solve(NM, Ms, y, alpha)
        pred = NM @ nm_coef + Ms @ mc_s
        mc_raw = mc_s / sd  # back to per-unit-spend-feature scale (for response curves)

        # per-week contributions
        contrib = pd.DataFrame({date_col: df[date_col].values})
        for j, ch in enumerate(m_names):
            contrib[ch] = mc_s[j] * Ms[:, j]
        contrib["baseline"] = NM @ nm_coef

        # honest holdout metrics at the chosen alpha
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
            starts = self.rng.integers(0, max(n - L, 1), size=n_blocks)
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
