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
    assert not fig.layout.title.text              # action titles live outside the figure
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


def test_combo_fig_has_secondary_axis_and_house_style():
    fig = theme.combo_fig([1, 2, 3], [10, 20, 30], [1.0, 2.0, 1.5],
                          bar_name="Spend", line_name="CPM",
                          bar_fmt="currency", line_fmt="currency", y2_title="CPM")
    # bar on primary axis, line on secondary
    assert fig.data[0].type == "bar"
    assert fig.data[1].type == "scatter" and fig.data[1].yaxis == "y2"
    # secondary axis is right-side, gridless, formatted; primary keeps the house grid
    assert fig.layout.yaxis2.side == "right"
    assert fig.layout.yaxis2.showgrid is False
    assert fig.layout.yaxis2.tickformat == "$,.0f"
    assert fig.layout.yaxis.gridcolor == theme.GRID
    assert fig.layout.font.family == theme.SANS


def test_dumbbell_fig_direction_coloring_and_shape():
    fig = theme.dumbbell_fig(["A", "B"], [0.10, 0.40], [0.30, 0.20],
                             from_name="Spend share", to_name="Claimed share")
    # 2 connector lines + 2 marker traces
    lines = [t for t in fig.data if t.mode == "lines"]
    assert len(lines) == 2
    assert lines[0].line.color == theme.POSITIVE      # A gained (0.10 -> 0.30)
    assert lines[1].line.color == theme.NEGATIVE      # B lost  (0.40 -> 0.20)
    markers = [t for t in fig.data if t.mode == "markers"]
    assert {t.name for t in markers} == {"Spend share", "Claimed share"}
    assert fig.layout.xaxis.tickformat == ".1%"


def test_paired_bars_share_categories_and_formats():
    fig = theme.paired_bars_fig(["meta", "linkedin"], [74.6, 205.2], [28944.0, 20517.0],
                                name1="Cost", name2="Spend",
                                customdata=["meta", "linkedin"])
    assert len(fig.data) == 2 and all(t.type == "bar" for t in fig.data)
    assert list(fig.data[0].customdata) == ["meta", "linkedin"]   # click -> channel key
    assert fig.data[0].text[0].startswith("$")                    # currency bar labels
    assert fig.layout.xaxis.tickformat == "$,.0f"
    assert fig.layout.xaxis2.tickformat == "$,.0f"


def test_annotate_uses_quiet_style():
    fig = theme.annotate(_fig(), x=2, y=20.0, text="peak week")
    ann = fig.layout.annotations[0]
    assert ann.text == "peak week"
    assert ann.font.color == theme.INK_SOFT
    assert ann.showarrow is True
