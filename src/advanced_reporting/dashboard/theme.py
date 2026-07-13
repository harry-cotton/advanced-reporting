"""Dashboard design tokens + the one house-style chart helper (redesign R1).

Every chart on every dashboard page goes through ``plotly_chart()`` — it enforces the
house style (fonts, gridlines, unified hover, margins, number formats) so pages never
hand-style Plotly figures. Chart TITLES deliberately live OUTSIDE the figure: the
design's rule is that every chart is headed by an ACTION TITLE (an insight sentence,
rendered in serif via ``action_title()``), never an axis-label-ish figure title.

Keep the palette in sync with ``.streamlit/config.toml``.
"""
from __future__ import annotations

import re
import zlib

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

# Muted RAG palette for the goal gauges (bullet bars). The BAND_FILL tints are pale so
# the value/number stays legible on top; VERDICT_INK is the (saturated) text accent for
# the number itself. Borrowed from the Power BI green/amber/red dials, flattened to fit
# the editorial house style — never use these saturated fills as chart series colors.
BAND_FILL = {"good": "#E4EEE4", "warn": "#F7EBD3", "bad": "#F4DEDE"}
VERDICT_INK = {"good": POSITIVE, "warn": "#B26A00", "bad": NEGATIVE}

# --- the reserved semantic palette (colour MEANS something; never reuse off-meaning) ---
# The signature honesty pair: platform-CLAIMED numbers are amber (self-graded),
# analytics-MEASURED numbers are ink blue. Never swap or reuse these for anything else —
# amber on a chart now reads "unverified", so spend/efficiency marks must NOT be amber.
CLAIMED = "#E08A00"
MEASURED = "#1F4E79"
# Money in (spend/cost) is context, not a verdict — a neutral warm graphite so it never
# competes with the claimed/measured semantic or a RAG hue. The default combo bar colour.
SPEND = "#7A7266"
# Efficiency/rate LINES on combos (CPM/CPC/CPA over time): a quiet near-ink, so a combo
# reads mono (graphite bar + dark line) rather than borrowing the claimed amber.
EFFICIENCY = "#2E2E3A"

# Confidence vocabulary for marks (the honesty product's visual grammar):
#   solid fill   = measured / actual
#   hatched      = modelled (MMM overlays — carry a 90% interval)
#   hollow/ghost = partial or incomplete (e.g. a flight's clipped opening/closing week)
GHOST = "#B8B2A6"          # hollow-marker ring / partial-period fill
HATCH = "/"                 # plotly pattern shape reserved for modelled series

# Editorial type: Fraunces (display serif) + Source Sans 3, loaded from Google Fonts in
# inject_css with system fallbacks — if the CDN is blocked (corporate proxy / offline),
# everything degrades to Georgia/Segoe and nothing breaks.
SERIF = "'Fraunces', Georgia, 'Times New Roman', serif"
SANS = "'Source Sans 3', 'Source Sans Pro', 'Segoe UI', sans-serif"

# Each channel gets ONE fixed identity colour, assigned here once and used ONLY in
# channel-comparison charts (spend mix, trends, ROAS) — never recoloured by value, so a
# channel keeps the same hue on every chart and page. Chosen to stay clear of the amber/
# ink honesty pair and of pure RAG red/green (which mean "verdict", not "channel").
CHANNEL_COLORS = {
    "google_search": "#4E79A7",
    "google_demandgen": "#76B7B2",
    "google_pmax": "#86BCB6",
    "meta": "#8E5AA8",          # violet
    "linkedin": "#4A90B8",
    "tiktok": "#3A3A3A",
    "display": "#2A9D8F",       # teal
    "email": "#4E79A7",         # slate blue
    "youtube": "#C15C4E",       # terracotta
    "organic_search": "#8A8F98",
    "direct": "#B9B2A6",
}
# Deterministic fallback ramp for unmapped channels — picked by a stable hash of the
# NAME (not call order), so an unmapped channel never swaps colour between charts.
_EXTRA_COLORS = ["#6B8E23", "#B8860B", "#9C6BA3", "#4682B4", "#B0623A", "#5A8A6E"]

# Validated bar+line combo palettes (bar = volume, line = efficiency) — one per combo so
# the charts don't all read as the same blue/amber. Every hex passes the dataviz checks
# (OKLCH lightness band, chroma ≥ 0.10, ≥ 3:1 contrast on the light surface); each pairs a
# cool bar with a warm line for strong bar-vs-line separation. Stable order — assign by
# chart, never recolour by value.
COMBO_PAIRS = {
    "blue_amber":  ("#2E6DA0", "#BE7A16"),
    "teal_rust":   ("#0C8A78", "#B4573A"),
    "violet_gold": ("#7A5EA8", "#9E7614"),
    "green_terra": ("#3E8A54", "#C1553F"),
}

# presentation names live with the (pure, testable) insight layer; re-exported here
from .insights import CHANNEL_LABELS, channel_label  # noqa: E402, F401

# d3-format strings for axes/hover, by semantic kind
TICKFORMAT = {"currency": "$,.0f", "count": ",.0f", "pct": ".1%", "ratio": ".2f"}


def channel_color(channel: str, i: int = 0) -> str:
    """The channel's fixed identity colour. ``i`` is ignored for mapped channels and,
    for unmapped ones, superseded by a stable name hash so colour never depends on the
    order channels happen to be drawn in (the old ``i % n`` caused chart-to-chart swaps).
    """
    if channel in CHANNEL_COLORS:
        return CHANNEL_COLORS[channel]
    idx = zlib.crc32(str(channel).encode()) % len(_EXTRA_COLORS)
    return _EXTRA_COLORS[idx]


# ---------------------------------------------------------------- page chrome
# Nav is grouped: the client STORY arc (what a CMO reads top-to-bottom), then a gutter,
# then the ANALYST tools. (label, path-relative-to-app.py, group).
_NAV_PAGES = [
    ("Exec Summary", "app.py",                 "story"),
    ("Channels",     "pages/1_Channels.py",    "story"),
    ("Audiences",    "pages/2_Audiences.py",   "story"),
    ("Incrementality", "pages/5_Results.py",   "story"),
    ("Data Quality", "pages/3_Data_Quality.py", "tools"),
    ("Explore",      "pages/4_Explore.py",      "tools"),
]


def inject_css() -> None:
    """Editorial typography: serif headlines, calmer captions. Call once per page."""
    st.markdown(
        f"""<style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Source+Sans+3:wght@400;600;700&display=swap');

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
    """Horizontal top navigation — the client story arc, a gutter, then analyst tools.

    Call immediately after inject_css() on every page. Paths are relative to
    the dashboard app.py entrypoint (src/advanced_reporting/dashboard/).
    """
    story = [p for p in _NAV_PAGES if p[2] == "story"]
    tools = [p for p in _NAV_PAGES if p[2] == "tools"]
    # a narrow empty gutter column visually separates the two groups
    cols = st.columns([*([1] * len(story)), 0.4, *([1] * len(tools))])
    i = 0
    for label, path, _ in story:
        with cols[i]:
            st.page_link(path, label=label, use_container_width=True)
        i += 1
    i += 1  # skip the gutter
    for label, path, _ in tools:
        with cols[i]:
            st.page_link(path, label=label, use_container_width=True)
        i += 1
    st.markdown(
        f'<div style="border-bottom:1px solid {GRID};margin:-0.5rem 0 1rem 0"></div>',
        unsafe_allow_html=True,
    )


def _escape_math(text: str) -> str:
    # Streamlit markdown parses $...$ as LaTeX — money amounts must be escaped
    return text.replace("$", "\\$")


def _html_inline(text: str) -> str:
    """Inline formatter for raw-HTML (``unsafe_allow_html``) blocks.

    Inside a raw HTML block Streamlit runs NEITHER markdown NOR KaTeX, so ``**bold**``
    would show its asterisks and ``$`` needs no LaTeX escaping (a backslash would show
    literally). So: HTML-escape ``&<>``, turn ``**x**`` into ``<strong>``, leave ``$`` raw.
    """
    s = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


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


def ai_block(md: str) -> None:
    """Render AI-drafted commentary as tinted, set-apart HTML.

    Converts our own markdown to HTML (where ``$`` is literal — Streamlit runs no KaTeX
    inside a raw-HTML block, so this fixes the garbled "35.2k∗∗in spend" the plain
    ``st.markdown`` produced) and DEMOTES ``##`` headings to a quiet small-caps sub-head so
    generated prose never competes with the page's real serif section titles.
    """
    import html as _h
    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = _h.escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"<em>\1</em>", line)
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f'<div style="font-family:{SANS};font-size:0.8rem;font-weight:700;'
                       f'letter-spacing:0.06em;text-transform:uppercase;color:{INK_SOFT};'
                       f'margin:1.1rem 0 0.3rem">{line[3:]}</div>')
        elif line.startswith("- "):
            if not in_list:
                out.append('<ul style="margin:0.3rem 0 0.3rem 1.1rem;padding:0">')
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        elif line:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f'<p style="margin:0.5rem 0">{line}</p>')
    if in_list:
        out.append("</ul>")
    st.markdown(
        f'<div style="background:{PAPER_TINT};border:1px solid {GRID};border-radius:8px;'
        f'padding:0.5rem 1.3rem 1rem">{"".join(out)}</div>',
        unsafe_allow_html=True)


def _delta_html(delta: str | None, delta_color: str, size: str = "0.82rem") -> str:
    """Shared delta chip. Direction is shown by an arrow OR a sign, never both (the old
    "▼ -12%" read as a double negative): graded metrics use a coloured arrow + unsigned
    magnitude; a neutral ("off") metric keeps the sign (no arrow to carry direction); a
    near-zero move reads "flat"."""
    if not delta:
        return ""
    pos, neg = delta.startswith("+"), delta.startswith("-")
    magnitude = delta.lstrip("+-")                  # "12% vs prior 4 wks"
    flat = magnitude.startswith("0%") or delta.startswith("flat") or not (pos or neg)
    if flat:
        color, arrow = INK_SOFT, ""
        text = _html_inline("flat vs prior 4 wks" if magnitude.startswith("0%") else delta)
    elif delta_color == "off":                       # neutral metric: keep the sign
        color, arrow, text = INK_SOFT, "", _html_inline(delta)
    elif delta_color == "inverse":                   # cost metric: down = good
        color, arrow = (POSITIVE, "▼ ") if neg else (NEGATIVE, "▲ ")
        text = _html_inline(magnitude)
    else:                                            # volume/rate: up = good
        color, arrow = (POSITIVE, "▲ ") if pos else (NEGATIVE, "▼ ")
        text = _html_inline(magnitude)
    return (f'<div style="font-family:{SANS};font-size:{size};'
            f'color:{color};margin-top:0.3rem">{arrow}{text}</div>')


def _sparkline_svg(values, color: str = ACCENT, w: int = 200, h: int = 42) -> str:
    """A tiny inline trend line for the hero stat — self-contained SVG (no extra chart
    element), so it lives inside the card's HTML."""
    vals = [float(v) for v in values if v is not None and v == v]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = " ".join(f"{i / (n - 1) * (w - 4) + 2:.1f},"
                   f"{h - 3 - (v - lo) / rng * (h - 6):.1f}" for i, v in enumerate(vals))
    lx = w - 2
    ly = h - 3 - (vals[-1] - lo) / rng * (h - 6)
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none" style="display:block;margin-top:10px">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.8" stroke-linejoin="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{color}"/></svg>')


def hero_card(label: str, value: str, delta: str | None = None,
              delta_color: str = "normal", spark=None, sub: str | None = None) -> None:
    """The Overview's primary KPI: an outsized value + optional sparkline, so the one
    number a CMO should leave with dominates the four-equal-tiles row it used to sit in.
    """
    spark_html = _sparkline_svg(spark) if spark is not None else ""
    sub_html = (f'<div style="font-family:{SANS};font-size:0.8rem;color:{INK_SOFT};'
                f'margin-top:0.15rem">{_html_inline(sub)}</div>') if sub else ""
    st.markdown(
        f'<div style="background:{PAPER};border:1px solid {GRID};border-left:3px solid '
        f'{ACCENT};border-radius:8px;padding:1.3rem 1.5rem;height:100%">'
        f'<div style="font-family:{SANS};font-size:0.8rem;font-weight:600;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:{INK_SOFT};'
        f'margin-bottom:0.4rem">{_html_inline(label)}</div>'
        f'<div style="font-family:{SANS};font-size:2.6rem;font-weight:700;'
        f'line-height:1.05;color:{INK};white-space:nowrap">{_html_inline(value)}</div>'
        f'{_delta_html(delta, delta_color, size="0.9rem")}{sub_html}{spark_html}</div>',
        unsafe_allow_html=True,
    )


def metric_card(label: str, value: str, delta: str | None = None,
                delta_color: str = "normal", help: str | None = None) -> None:
    """Styled KPI card: border, large bold value, colored delta with arrow.

    ``delta_color``: "normal" (up=green), "inverse" (up=red, for cost metrics),
    "off" (neutral, no arrow).
    """
    delta_html = _delta_html(delta, delta_color)

    esc_help = (str(help).replace("&", "&amp;").replace('"', "&quot;")
                .replace("<", "&lt;").replace(">", "&gt;")) if help else ""
    tooltip = f' title="{esc_help}"' if help else ""
    st.markdown(
        f'<div{tooltip} style="background:{PAPER};border:1px solid {GRID};'
        f'border-radius:8px;padding:1.1rem 1.25rem">'
        f'<div style="font-family:{SANS};font-size:0.78rem;font-weight:600;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:{INK_SOFT};'
        f'margin-bottom:0.35rem">{_html_inline(label)}</div>'
        f'<div style="font-family:{SANS};font-size:1.55rem;font-weight:700;'
        f'line-height:1.15;color:{INK};white-space:nowrap">{_html_inline(value)}</div>'
        f'{delta_html}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- goal gauges (bullets)
# Flat "bullet bar" gauges — the editorial re-draw of the Power BI dials. A pace_bullet
# shows a value filling toward a goal (a tick marks 100%); a rag_bullet shows a value
# marker sitting over muted good/amber/bad zones. Both are the same primitive; they sit
# in a row like the old gauge strip but read as quiet KPI widgets, not skeuomorphic dials.
def _bullet_head(label: str, value_str: str, verdict: str | None = None) -> str:
    color = VERDICT_INK.get(verdict, INK)
    return (
        f'<div style="font-family:{SANS};font-size:0.72rem;font-weight:600;'
        f'letter-spacing:0.04em;text-transform:uppercase;color:{INK_SOFT}">{label}</div>'
        f'<div style="font-family:{SANS};font-size:1.35rem;font-weight:700;line-height:1.15;'
        f'color:{color};margin:0.1rem 0 0.4rem 0;white-space:nowrap">'
        f'{_html_inline(value_str)}</div>')


def _bullet_note(note: str | None) -> str:
    if not note:
        return ""
    return (f'<div style="font-family:{SANS};font-size:0.72rem;color:{INK_SOFT};'
            f'margin-top:0.35rem">{_html_inline(note)}</div>')


def _marker(pos_pct: float) -> str:
    """A thin ink tick that overhangs the track (sibling of the clipped track div)."""
    return (f'<div style="position:absolute;left:{pos_pct:.1f}%;top:-2px;height:13px;'
            f'width:2px;background:{INK};transform:translateX(-1px)"></div>')


def pace_bullet(label: str, value_str: str, fill_frac: float, goal_frac: float,
                note: str | None = None) -> None:
    """Progress-to-goal bar: fill = value share of the track, tick = the goal (100%)."""
    fill = max(0.0, min(fill_frac, 1.0)) * 100
    goal = max(0.0, min(goal_frac, 1.0)) * 100
    st.markdown(
        f'<div style="padding:0.2rem 0">{_bullet_head(label, value_str)}'
        f'<div style="position:relative">'
        f'<div style="position:relative;height:9px;border-radius:5px;overflow:hidden;'
        f'background:{PAPER_TINT};border:1px solid {GRID}">'
        f'<div style="position:absolute;left:0;top:0;bottom:0;width:{fill:.1f}%;'
        f'background:{ACCENT}"></div></div>{_marker(goal)}</div>'
        f'{_bullet_note(note)}</div>',
        unsafe_allow_html=True,
    )


def rag_bullet(label: str, value_str: str, pos: float,
               band_stops: list[tuple[float, float, str]], verdict: str | None = None,
               note: str | None = None) -> None:
    """Value-vs-threshold bar: muted good/amber/bad zones with a marker at the value.

    ``band_stops``: ``[(start_frac, end_frac, "good"|"warn"|"bad"), ...]`` left→right.
    """
    segs = "".join(
        f'<div style="position:absolute;left:{s * 100:.1f}%;top:0;bottom:0;'
        f'width:{max(0.0, e - s) * 100:.1f}%;background:{BAND_FILL.get(name, PAPER_TINT)}">'
        f'</div>'
        for s, e, name in band_stops)
    st.markdown(
        f'<div style="padding:0.2rem 0">{_bullet_head(label, value_str, verdict)}'
        f'<div style="position:relative">'
        f'<div style="position:relative;height:9px;border-radius:5px;overflow:hidden;'
        f'border:1px solid {GRID}">{segs}</div>'
        f'{_marker(min(max(pos, 0.0), 1.0) * 100)}</div>'
        f'{_bullet_note(note)}</div>',
        unsafe_allow_html=True,
    )


def render_bullets(pace: list[dict] = (), rag: list[dict] = ()) -> None:
    """Render a tier scorecard's pacing + RAG bullets (from ``insights.tier_scorecard``).

    The single place bullets are drawn, so every page's scorecard looks identical.
    """
    for p in pace:
        pace_bullet(p["label"], p["value_str"], p["fill_frac"], p["goal_frac"],
                    note=p.get("note"))
    for r in rag:
        rag_bullet(r["label"], r["value_str"], r["pos"], r["band_stops"],
                   verdict=r.get("verdict"), note=r.get("note"))


def metric_grid(title: str | None, items: list[tuple[str, str]], cols: int = 2) -> None:
    """Dense (value-over-label) totals card — the "Media Totals" block, house-styled."""
    cells = "".join(
        f'<div><div style="font-family:{SANS};font-size:1.1rem;font-weight:700;'
        f'color:{INK};line-height:1.2;white-space:nowrap">{_html_inline(value)}</div>'
        f'<div style="font-family:{SANS};font-size:0.68rem;font-weight:600;'
        f'letter-spacing:0.03em;text-transform:uppercase;color:{INK_SOFT};'
        f'margin-top:0.1rem">{_html_inline(label)}</div></div>'
        for label, value in items)
    head = (f'<div style="font-family:{SANS};font-size:0.72rem;font-weight:600;'
            f'letter-spacing:0.04em;text-transform:uppercase;color:{INK_SOFT};'
            f'margin-bottom:0.7rem">{title}</div>') if title else ""
    st.markdown(
        f'<div style="background:{PAPER};border:1px solid {GRID};border-radius:8px;'
        f'padding:1.1rem 1.25rem">{head}'
        f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);'
        f'gap:0.9rem 1.1rem">{cells}</div></div>',
        unsafe_allow_html=True,
    )


def lede(text: str) -> None:
    """Leadership abstract: serif, accent left-border, sits above the insight blocks."""
    st.markdown(
        f'<div style="font-family:{SERIF};font-size:1.1rem;line-height:1.65;'
        f'color:{INK};border-left:3px solid {ACCENT};'
        f'padding:0.5rem 0 0.5rem 1rem;margin:0.25rem 0 0.75rem 0">'
        f'{_html_inline(text)}</div>',
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
                 height: int = 380, legend: bool = True, select_key: str | None = None):
    """THE way to render a chart: house style + quiet chrome. All pages use this.

    Pass ``select_key`` to make the chart CLICKABLE (cross-filtering): the call then
    returns Streamlit's selection event — feed it to ``filters.handle_channel_click``.
    Clickable traces must carry the channel key in ``customdata`` (one entry per point).
    """
    style_fig(fig, yfmt=yfmt, xfmt=xfmt, height=height, legend=legend)
    # theme=None: the house style owns the figure — Streamlit's plotly template
    # otherwise overrides fonts/colors and injects a stray empty title
    kwargs = dict(use_container_width=True, theme=None,
                  config={"displayModeBar": False})
    if select_key:
        return st.plotly_chart(fig, key=select_key, on_select="rerun",
                               selection_mode="points", **kwargs)
    st.plotly_chart(fig, **kwargs)
    return None


# ---------------------------------------------------------------- combo (bar + line)
def combo_fig(x, bar_y, line_y, *, bar_name: str, line_name: str,
              bar_fmt: str = "count", line_fmt: str = "currency",
              y2_title: str | None = None, bar_color: str | None = None,
              line_color: str | None = None, bar_text=None,
              height: int = 340) -> go.Figure:
    """Dual-axis bar+line combo (volume on the left axis, an efficiency line on the
    right) — the Power BI "Spend & CPM" pattern, house-styled. Pure: returns the figure.

    ``bar_fmt``/``line_fmt`` are semantic kinds from TICKFORMAT (left axis = bars).
    """
    fig = go.Figure()
    # bars = spend/volume: neutral graphite (money is context, not a verdict — never the
    # amber "claimed" or a RAG hue). rounded data-ends, a touch of translucency.
    fig.add_bar(x=x, y=bar_y, name=bar_name, opacity=0.95,
                marker=dict(color=bar_color or SPEND, cornerradius=6,
                            line=dict(width=0)),
                text=bar_text, textposition="outside" if bar_text is not None else None,
                cliponaxis=False)
    # line = efficiency: a quiet near-ink so the combo reads mono (graphite bar + dark
    # line), not a borrowed-amber rainbow. ringed markers read cleanly over the bars.
    fig.add_scatter(x=x, y=line_y, name=line_name, mode="lines+markers", yaxis="y2",
                    line=dict(color=line_color or EFFICIENCY, width=2.5, shape="spline",
                              smoothing=0.6),
                    marker=dict(size=8, color=line_color or EFFICIENCY,
                                line=dict(color=PAPER, width=1.6)))
    style_fig(fig, yfmt=bar_fmt, height=height)
    # a secondary axis + outside bar labels need room on the right, or tick labels clip
    # to a bare "$" and the last bar's value is cut off (observed on Channels/Explore).
    fig.update_layout(bargap=0.32, margin=dict(l=8, r=54, t=30, b=8))
    # headroom so the efficiency line doesn't hug the plot ceiling (list(): line_y may be
    # a pandas Series, whose truthiness is ambiguous — never use ``line_y or []``)
    _lvals = [float(v) for v in list(line_y) if v is not None and v == v] \
        if line_y is not None else []
    _lmax = max(_lvals) if _lvals else 1.0
    fig.update_layout(yaxis2=dict(
        overlaying="y", side="right", showgrid=False, zeroline=False,
        range=[0, _lmax * 1.25 or 1.0], automargin=True,
        tickformat=TICKFORMAT.get(line_fmt, line_fmt),
        tickfont=dict(size=12, color=INK_SOFT),
        title=dict(text=y2_title or line_name,
                   font=dict(family=SANS, size=12, color=INK_SOFT))))
    return fig


def combo(x, bar_y, line_y, **kwargs):
    """Render a bar+line combo: build via ``combo_fig`` + house-style chrome.

    ``customdata=[...]`` (per bar) + ``select_key=`` make the bars clickable.
    """
    height = kwargs.pop("height", 340)
    select_key = kwargs.pop("select_key", None)
    customdata = kwargs.pop("customdata", None)
    fig = combo_fig(x, bar_y, line_y, height=height, **kwargs)
    if customdata is not None:
        fig.data[0].customdata = list(customdata)
    kw = dict(use_container_width=True, theme=None, config={"displayModeBar": False})
    if select_key:
        return st.plotly_chart(fig, key=select_key, on_select="rerun",
                               selection_mode="points", **kw)
    st.plotly_chart(fig, **kw)
    return None


# ---------------------------------------------------------------- readable comparisons
def dumbbell_fig(labels, x_from, x_to, *, from_name: str, to_name: str,
                 fmt: str = "pct", height: int | None = None) -> go.Figure:
    """Dumbbell / slope-in-x chart: one row per item, a quiet dot (from) connected to a
    strong dot (to). Replaces crowded few-point scatters — gain/loss reads instantly
    (green segment = ``to`` above ``from``, red = below).
    """
    fig = go.Figure()
    for lab, a, b in zip(labels, x_from, x_to):
        fig.add_scatter(x=[a, b], y=[lab, lab], mode="lines", showlegend=False,
                        line=dict(color=POSITIVE if b >= a else NEGATIVE, width=3),
                        opacity=0.6, hoverinfo="skip")
    fig.add_scatter(x=list(x_from), y=list(labels), mode="markers", name=from_name,
                    marker=dict(size=11, color=PAPER,
                                line=dict(color=INK_SOFT, width=2)))
    fig.add_scatter(x=list(x_to), y=list(labels), mode="markers", name=to_name,
                    marker=dict(size=12, color=ACCENT))
    style_fig(fig, xfmt=fmt, height=height or (110 + 44 * len(list(labels))))
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(showgrid=False, tickformat=None, autorange="reversed")
    # re-assert the x tickformat (this second update_xaxes would otherwise leave the axis
    # showing raw decimals like "0 0.2 0.4" instead of the intended percentages)
    fig.update_xaxes(showgrid=True, gridcolor=GRID,
                     tickformat=TICKFORMAT.get(fmt, fmt))
    return fig


def paired_bars_fig(labels, x1, x2, *, name1: str, name2: str,
                    fmt1: str = "currency", fmt2: str = "currency",
                    colors1=None, colors2=None, customdata=None,
                    height: int | None = None) -> go.Figure:
    """Two aligned horizontal bar panels sharing the category axis — the readable
    replacement for a few-point bubble scatter (rank on the left, context on the right).
    """
    from plotly.subplots import make_subplots

    def _txt(vals, fmt):
        dollar = "$" if str(fmt).startswith(("currency", "$")) else ""
        return [f"{dollar}{v:,.2f}" if abs(v) < 100 else f"{dollar}{v:,.0f}"
                for v in vals]

    labels = list(labels)
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.06,
                        subplot_titles=(name1, name2))
    fig.add_bar(y=labels, x=list(x1), orientation="h", name=name1,
                marker_color=colors1 or ACCENT, customdata=customdata,
                text=_txt(x1, fmt1), textposition="outside",
                cliponaxis=False, row=1, col=1)
    fig.add_bar(y=labels, x=list(x2), orientation="h", name=name2,
                marker_color=colors2 or GRID, customdata=customdata,
                text=_txt(x2, fmt2), textposition="outside",
                cliponaxis=False, row=1, col=2)
    style_fig(fig, height=height or (120 + 48 * len(labels)), legend=False)
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(showgrid=False, autorange="reversed")
    fig.update_xaxes(showgrid=True, gridcolor=GRID,
                     tickformat=TICKFORMAT.get(fmt1, fmt1), row=1, col=1)
    fig.update_xaxes(showgrid=True, gridcolor=GRID,
                     tickformat=TICKFORMAT.get(fmt2, fmt2), row=1, col=2)
    for ann in fig.layout.annotations:        # subplot titles -> quiet house captions
        ann.font = dict(family=SANS, size=12, color=INK_SOFT)
    return fig
