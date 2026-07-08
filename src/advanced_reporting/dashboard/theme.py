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

# presentation names live with the (pure, testable) insight layer; re-exported here
from .insights import CHANNEL_LABELS, channel_label  # noqa: E402, F401

# d3-format strings for axes/hover, by semantic kind
TICKFORMAT = {"currency": "$,.0f", "count": ",.0f", "pct": ".1%", "ratio": ".2f"}


def channel_color(channel: str, i: int = 0) -> str:
    return CHANNEL_COLORS.get(channel, _EXTRA_COLORS[i % len(_EXTRA_COLORS)])


# ---------------------------------------------------------------- page chrome
# Pages in display order: (label, path-relative-to-app.py)
_NAV_PAGES = [
    ("Exec Summary", "app.py"),
    ("Channels",     "pages/1_Channels.py"),
    ("Audiences",    "pages/2_Audiences.py"),
    ("Data Quality", "pages/3_Data_Quality.py"),
    ("Explore",      "pages/4_Explore.py"),
]


def inject_css() -> None:
    """Editorial typography: serif headlines, calmer captions. Call once per page."""
    st.markdown(
        f"""<style>
        h1, h2, h3,
        [data-testid="stHeading"] h1, [data-testid="stHeading"] h2,
        [data-testid="stHeading"] h3,
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {{
            font-family: {SERIF} !important; font-weight: 600;
            letter-spacing: -0.01em; color: {INK};
        }}
        [data-testid="stCaptionContainer"] {{ color: {INK_SOFT}; }}
        [data-testid="stMetricLabel"] {{ color: {INK_SOFT}; }}

        /* Hide Streamlit's auto-generated sidebar page list — nav_bar() replaces it */
        [data-testid="stSidebarNavItems"],
        [data-testid="stSidebarNavSeparator"] {{ display: none !important; }}

        /* Style page_link nav items to look like tab buttons */
        [data-testid="stPageLink"] a {{
            display: block; text-align: center;
            padding: 0.35rem 0.5rem;
            border-bottom: 2px solid transparent;
            color: {INK_SOFT}; text-decoration: none;
            font-family: {SANS}; font-size: 0.88rem; font-weight: 500;
            transition: color 0.15s, border-color 0.15s;
        }}
        [data-testid="stPageLink"] a:hover {{
            color: {ACCENT}; border-bottom-color: {ACCENT};
        }}
        </style>""",
        unsafe_allow_html=True,
    )


def nav_bar() -> None:
    """Horizontal top navigation — row of page links + a separator line.

    Call immediately after inject_css() on every page. Paths are relative to
    the dashboard app.py entrypoint (src/advanced_reporting/dashboard/).
    """
    cols = st.columns(len(_NAV_PAGES))
    for col, (label, path) in zip(cols, _NAV_PAGES):
        with col:
            st.page_link(path, label=label, use_container_width=True)
    st.markdown(
        f'<div style="border-bottom:1px solid {GRID};margin:-0.5rem 0 1rem 0"></div>',
        unsafe_allow_html=True,
    )


def _escape_math(text: str) -> str:
    # Streamlit markdown parses $...$ as LaTeX — money amounts must be escaped
    return text.replace("$", "\\$")


def action_title(insight: str, sub: str | None = None) -> None:
    """A chart's heading: an INSIGHT SENTENCE in serif, never a label.

    If a section can't state an insight, it doesn't belong on the narrative page.
    """
    st.markdown(f"### {_escape_math(insight)}")
    if sub:
        st.caption(sub)


def prose(text: str) -> None:
    """Render a woven narrative paragraph (markdown, money-safe)."""
    st.markdown(_escape_math(text))


def metric_card(label: str, value: str, delta: str | None = None,
                delta_color: str = "normal", help: str | None = None) -> None:
    """Styled KPI card: border, large bold value, colored delta with arrow.

    ``delta_color``: "normal" (up=green), "inverse" (up=red, for cost metrics),
    "off" (neutral, no arrow).
    """
    delta_html = ""
    if delta:
        pos = delta.startswith("+")
        neg = delta.startswith("-")
        if delta_color == "off":
            color, arrow = INK_SOFT, ""
        elif delta_color == "inverse":
            color = POSITIVE if neg else (NEGATIVE if pos else INK_SOFT)
            arrow = "▼ " if neg else ("▲ " if pos else "")
        else:
            color = POSITIVE if pos else (NEGATIVE if neg else INK_SOFT)
            arrow = "▲ " if pos else ("▼ " if neg else "")
        delta_html = (f'<div style="font-family:{SANS};font-size:0.82rem;'
                      f'color:{color};margin-top:0.3rem">'
                      f'{arrow}{_escape_math(delta)}</div>')

    tooltip = f' title="{help}"' if help else ""
    st.markdown(
        f'<div{tooltip} style="background:{PAPER};border:1px solid {GRID};'
        f'border-radius:8px;padding:1.1rem 1.25rem">'
        f'<div style="font-family:{SANS};font-size:0.78rem;font-weight:600;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:{INK_SOFT};'
        f'margin-bottom:0.35rem">{label}</div>'
        f'<div style="font-family:{SANS};font-size:1.9rem;font-weight:700;'
        f'line-height:1.1;color:{INK}">{_escape_math(value)}</div>'
        f'{delta_html}</div>',
        unsafe_allow_html=True,
    )


def lede(text: str) -> None:
    """Leadership abstract: serif, accent left-border, sits above the insight blocks."""
    st.markdown(
        f'<div style="font-family:{SERIF};font-size:1.1rem;line-height:1.65;'
        f'color:{INK};border-left:3px solid {ACCENT};'
        f'padding:0.5rem 0 0.5rem 1rem;margin:0.25rem 0 0.75rem 0">'
        f'{_escape_math(text)}</div>',
        unsafe_allow_html=True,
    )


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
        title_text="",                    # action titles live outside the figure
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None,
                    font=dict(size=12, color=INK_SOFT)),
        showlegend=legend,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID,
                     ticks="outside", tickcolor=GRID, automargin=True,
                     tickformat=TICKFORMAT.get(xfmt, xfmt))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", ticks="", automargin=True,
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
    # theme=None: the house style owns the figure — Streamlit's plotly template
    # otherwise overrides fonts/colors and injects a stray empty title
    st.plotly_chart(fig, use_container_width=True, theme=None,
                    config={"displayModeBar": False})
