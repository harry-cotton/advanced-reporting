"""Reusable media-transform math: adstock (carryover) and saturation (diminishing returns)."""
from __future__ import annotations
import numpy as np


def geometric_adstock(x: np.ndarray, decay: float, max_lag: int = 8,
                      normalize: bool = True) -> np.ndarray:
    """Geometric carryover. Each period gets a weighted sum of current + past spend.

    decay in [0, 1): higher = longer-lasting effect. Weights are decay**lag.
    """
    x = np.asarray(x, dtype=float)
    weights = decay ** np.arange(max_lag + 1)
    if normalize:
        weights = weights / weights.sum()
    out = np.zeros_like(x)
    for lag, w in enumerate(weights):
        if w == 0:
            continue
        shifted = np.zeros_like(x)
        if lag == 0:
            shifted = x
        else:
            shifted[lag:] = x[:-lag]
        out += w * shifted
    return out


def hill_saturation(x: np.ndarray, half_sat: float, slope: float = 1.0) -> np.ndarray:
    """Hill (S-curve) saturation in [0, 1). half_sat = input at which response is 0.5.

    slope > 1 gives a steeper S-curve. Captures diminishing returns to spend.
    """
    x = np.asarray(x, dtype=float)
    x = np.clip(x, 0, None)
    hs = max(half_sat, 1e-9)
    xs = x ** slope
    return xs / (xs + hs ** slope)
