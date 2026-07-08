"""Global sidebar filters (date range + channel) shared across the dashboard pages.

Selections persist in ``st.session_state`` under stable keys, so switching tabs keeps
the same filter — the Power BI Date/Channel slicer behaviour. Each page calls
``sidebar_filters()`` to render the controls, then ``apply()`` to its own frame.

``apply()`` is pure pandas (no Streamlit) so it's unit-testable.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

_K_DATES = "flt_date_range"
_K_CHANNELS = "flt_channels"


def sidebar_filters(channels, date_min, date_max, *, show_channels: bool = True):
    """Render the shared Date range (+ Channel) controls; return ``(date_range, channels)``.

    Widget state is keyed so the selection carries across page switches. ``channels`` is
    sanitised against the current options each run so a stale pick never errors the widget.
    """
    st.sidebar.markdown("**Filters**")
    st.sidebar.caption("Apply across every tab.")

    dkw = {} if _K_DATES in st.session_state else {"value": (date_min, date_max)}
    date_range = st.sidebar.date_input("Date range", min_value=date_min,
                                       max_value=date_max, key=_K_DATES, **dkw)

    selected = None
    if show_channels:
        opts = sorted(channels)
        if _K_CHANNELS in st.session_state:
            st.session_state[_K_CHANNELS] = [
                c for c in st.session_state[_K_CHANNELS] if c in opts]
            ckw = {}
        else:
            ckw = {"default": opts}
        selected = st.sidebar.multiselect("Channels", opts, key=_K_CHANNELS, **ckw)
    return date_range, selected


def apply(df: pd.DataFrame, date_range=None, channels=None, *,
          date_col: str = "date") -> pd.DataFrame:
    """Filter ``df`` by the shared selection. Empty/None channels = all channels."""
    out = df
    if channels:                       # empty selection means "all", not "none"
        out = out[out["channel"].isin(channels)]
    if (isinstance(date_range, (tuple, list)) and len(date_range) == 2
            and date_col in out.columns):
        lo, hi = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        out = out[(out[date_col] >= lo) & (out[date_col] <= hi)]
    return out
