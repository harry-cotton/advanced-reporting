"""Theme/chart-standard tests — pure Plotly layout logic, no Streamlit runtime."""
from __future__ import annotations

import plotly.graph_objects as go

from advanced_reporting.dashboard import theme


def _fig() -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=[1, 2, 3], y=[10.0, 20.0, 15.0])
    return fig


def test_style_fig_enforces_house_style():
    fig = theme.style_fig(_fig(), yfmt="currency")
    assert fig.layout.font.family == theme.SANS
    assert fig.layout.hovermode == "x unified"
    assert fig.layout.title.text is None          # action titles live outside the figure
    assert fig.layout.yaxis.tickformat == "$,.0f"
    assert fig.layout.yaxis.gridcolor == theme.GRID
    assert fig.layout.xaxis.showgrid is False


def test_style_fig_accepts_raw_d3_formats():
    fig = theme.style_fig(_fig(), yfmt=".3f", xfmt="pct")
    assert fig.layout.yaxis.tickformat == ".3f"
    assert fig.layout.xaxis.tickformat == ".1%"


def test_channel_colors_and_labels():
    assert theme.channel_color("meta") == theme.CHANNEL_COLORS["meta"]
    # unmapped channels get a deterministic fallback, never a KeyError
    assert theme.channel_color("bing_ads", 1) == theme.channel_color("bing_ads", 1)
    assert theme.channel_label("google_demandgen") == "Google Demand Gen"
    assert theme.channel_label("some_new_channel") == "Some New Channel"


def test_claimed_vs_measured_tokens_are_distinct():
    # the signature honesty pair must never collapse into one color
    assert theme.CLAIMED != theme.MEASURED
    assert theme.CLAIMED not in theme.CHANNEL_COLORS.values()


def test_annotate_uses_quiet_style():
    fig = theme.annotate(_fig(), x=2, y=20.0, text="peak week")
    ann = fig.layout.annotations[0]
    assert ann.text == "peak week"
    assert ann.font.color == theme.INK_SOFT
    assert ann.showarrow is True
