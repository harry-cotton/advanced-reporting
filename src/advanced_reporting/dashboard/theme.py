"""Dashboard design tokens + the one house-style chart helper (redesign R1).

Every chart on every dashboard page goes through ``plotly_chart()`` — it enforces the
house style (fonts, gridlines, unified hover, margins, number formats) so pages never
hand-style Plotly figures. Chart TITLES deliberately live OUTSIDE the figure: the
design's rule is that every chart is headed by an ACTION TITLE (an insight sentence,
rendered in serif via ``action_title()``), never an axis-label-ish figure title.

Keep the palette in sync with ``.streamlit/config.toml``.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------- design tokens
INK = "#1C1C28"            # body text
INK_SOFT = "#5A5A6E"       # secondary text / captions
PAPER = "#FFFFFF"
PAPER_TINT = "#F6F4EF"     # cards / asides (matches secondaryBackgroundColor)
ACCENT = "#1F4E79"         # house accent (aligns with the naming generator's deck blue)
GRID = "#E8E5DE"           # faint warm gridlines
POSITIVE = "#2E7D32"
NEGATIVE = "#C62828"

# The signature honesty pair: platform-CLAIMED numbers are amber (self-graded),
# analytics-MEASURED numbers are ink blue. Never swap or reuse these for anything else.
CLAIMED = "#E08A00"
MEASURED = "#1F4E79"

SERIF = "Georgia, 'Times New Roman', serif"          # editorial headlines
SANS = "'Source Sans Pro', 'Segoe UI', sans-serif"   # body / chart text

CHANNEL_COLORS = {
    "google_search": "#3B6FB5",
    "google_demandgen": "#5FA8A0",
    "google_pmax": "#7C9ED9",
    "meta": "#8E5AA8",
    "linkedin": "#0A66C2",
    "tiktok": "#3A3A3A",
    "organic_search": "#8A8F98",
    "direct": "#B9B2A6",
}
_EXTRA_COLORS = ["#C25B4E", "#6B8E23", "#B8860B", "#4682B4"]  # unmapped channels

CHANNEL_LABELS = {
    "google_search": "Google Search",
    "google_demandgen": "Google Demand Gen",
    "google_pmax": "Google PMax",
    "meta": "Meta",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "organic_search": "Organic search",
    "direct": "Direct",
}

# d3-format strings for axes/hover, by semantic kind
TICKFORMAT = {"currency": "$,.0f", "count": ",.0f", "pct": ".1%", "ratio": ".2f"}


def channel_color(channel: str, i: int = 0) -> str:
    return CHANNEL_COLORS.get(channel, _EXTRA_COLORS[i % len(_EXTRA_COLORS)])


def channel_label(channel: str) -> str:
    return CHANNEL_LABELS.get(channel, str(channel).replace("_", " ").title())


# ---------------------------------------------------------------- page chrome
def inject_css() -> None:
    """Editorial typography: serif headlines, calmer captions. Call once per page."""
    st.markdown(
        f"""<style>
        h1, h2, h3 {{ font-family: {SERIF}; font-weight: 600; letter-spacing: -0.01em;
                      color: {INK}; }}
        [data-testid="stCaptionContainer"] {{ color: {INK_SOFT}; }}
        [data-testid="stMetricLabel"] {{ color: {INK_SOFT}; }}
        </style>""",
        unsafe_allow_html=True,
    )


def action_title(insight: str, sub: str | None = None) -> None:
    """A chart's heading: an INSIGHT SENTENCE in serif, never a label.

    If a section can't state an insight, it doesn't belong on the narrative page.
    """
    st.markdown(f"### {insight}")
    if sub:
        st.caption(sub)


# ---------------------------------------------------------------- the chart standard
def style_fig(fig: go.Figure, *, yfmt: str | None = None, xfmt: str | None = None,
              height: int = 380, legend: bool = True) -> go.Figure:
    """Apply the house style in place (and return the figure).

    ``yfmt``/``xfmt`` are semantic kinds from TICKFORMAT ("currency", "count", "pct",
    "ratio") or raw d3-format strings.
    """
    fig.update_layout(
        font=dict(family=SANS, size=13, color=INK),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=28, b=8), height=height,
        hovermode="x unified",
        hoverlabel=dict(font=dict(family=SANS, size=12), bgcolor=PAPER,
                        bordercolor=GRID, font_color=INK),
        title=None,                       # action titles live outside the figure
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None,
                    font=dict(size=12, color=INK_SOFT)),
        showlegend=legend,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID,
                     ticks="outside", tickcolor=GRID,
                     tickformat=TICKFORMAT.get(xfmt, xfmt))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", ticks="",
                     tickformat=TICKFORMAT.get(yfmt, yfmt), rangemode="tozero")
    return fig


def annotate(fig: go.Figure, x, y, text: str, *, above: bool = True) -> go.Figure:
    """House annotation: quiet gray note with a thin pointer, for story beats on charts."""
    fig.add_annotation(
        x=x, y=y, text=text, showarrow=True, arrowhead=0, arrowwidth=1,
        arrowcolor=INK_SOFT, ax=0, ay=-42 if above else 42,
        font=dict(family=SANS, size=12, color=INK_SOFT),
        bgcolor="rgba(255,255,255,0.85)", bordercolor=GRID, borderwidth=1,
        borderpad=4)
    return fig


def plotly_chart(fig: go.Figure, *, yfmt: str | None = None, xfmt: str | None = None,
                 height: int = 380, legend: bool = True) -> None:
    """THE way to render a chart: house style + quiet chrome. All pages use this."""
    style_fig(fig, yfmt=yfmt, xfmt=xfmt, height=height, legend=legend)
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})
