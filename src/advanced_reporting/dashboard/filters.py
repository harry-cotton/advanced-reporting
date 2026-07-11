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
# Cross-filter staging: a chart click can't write the multiselect's key after the
# widget has rendered this run (Streamlit forbids it), so clicks stage the new value
# here + st.rerun(); sidebar_filters applies it BEFORE the widget instantiates.
_K_PENDING = "flt_channels_pending"


def toggle_channel(current: list | None, clicked: str) -> list:
    """Cross-filter toggle: click a channel to focus on it, click it again to clear.

    Pure (no Streamlit) — empty list means "all channels" (the multiselect convention).
    """
    if current and set(current) == {clicked}:
        return []
    return [clicked]


def sidebar_filters(channels, date_min, date_max, *, show_channels: bool = True):
    """Render the shared Date range (+ Channel) controls; return ``(date_range, channels)``.

    Widget state is keyed so the selection carries across page switches. ``channels`` is
    sanitised against the current options each run so a stale pick never errors the widget.
    """
    st.sidebar.markdown("**Filters**")
    st.sidebar.caption("Apply across every tab. Clicking a channel in any chart "
                       "focuses on it; clicking it again clears.")

    dkw = {} if _K_DATES in st.session_state else {"value": (date_min, date_max)}
    date_range = st.sidebar.date_input("Date range", min_value=date_min,
                                       max_value=date_max, key=_K_DATES, **dkw)

    selected = None
    if show_channels:
        opts = sorted(channels)
        if _K_PENDING in st.session_state:            # a chart click from last run
            st.session_state[_K_CHANNELS] = st.session_state.pop(_K_PENDING)
        if _K_CHANNELS in st.session_state:
            st.session_state[_K_CHANNELS] = [
                c for c in st.session_state[_K_CHANNELS] if c in opts]
            ckw = {}
        else:
            ckw = {"default": opts}
        selected = st.sidebar.multiselect("Channels", opts, key=_K_CHANNELS, **ckw)
    return date_range, selected


def handle_channel_click(event) -> None:
    """Turn a house-chart selection event into the global channel cross-filter.

    ``event`` is the return of ``theme.plotly_chart(..., select_key=...)`` (or None).
    The clicked trace point must carry the channel key in ``customdata``. Toggles the
    shared channel filter and reruns so every chart on the page refilters.
    """
    if not event:
        return
    try:
        points = event.selection.points
    except AttributeError:
        points = (event.get("selection") or {}).get("points") or []
    if not points:
        return
    ch = points[0].get("customdata")
    if isinstance(ch, (list, tuple)):
        ch = ch[0] if ch else None
    if not ch:
        return
    new = toggle_channel(st.session_state.get(_K_CHANNELS), str(ch))
    if new != st.session_state.get(_K_CHANNELS):
        st.session_state[_K_PENDING] = new
        st.rerun()


def focus_chip() -> None:
    """Show a dismissible "focused on X" chip when the cross-filter is a single channel."""
    from .insights import channel_label
    sel = st.session_state.get(_K_CHANNELS)
    if sel and len(sel) == 1:
        if st.button(f"✕  Focused on {channel_label(sel[0])} — show all channels",
                     key="_xf_clear", type="secondary"):
            st.session_state[_K_PENDING] = []
            st.rerun()


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
