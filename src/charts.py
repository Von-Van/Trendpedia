"""Shared Plotly styling so every page reads as one system.

Palette is the validated eight-slot categorical set (worst adjacent CVD dE 9.1
light / 8.4 dark). Three light-mode slots sit under 3:1 contrast, so every view
that uses them also ships direct labels or a table — that is the relief rule,
not an optional nicety.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Categorical identity. Assigned in fixed order, never cycled: a ninth group
# folds into "Other" rather than inventing a hue.
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
              "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500",
             "#d55181", "#008300", "#9085e9", "#e66767"],
}
OTHER = {"light": "#8a8880", "dark": "#7d7b73"}

SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
              "#256abf", "#184f95", "#0d366b"]
DIVERGING = [[0.0, "#0d366b"], [0.25, "#3987e5"], [0.5, "#f0efec"],
             [0.75, "#e34948"], [1.0, "#8f1f1f"]]

INK = {
    "light": {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#8a8880",
              "grid": "#e6e5e1", "surface": "rgba(0,0,0,0)", "axis": "#c9c8c3",
              "surface_solid": "#ffffff"},
    "dark": {"primary": "#ffffff", "secondary": "#c3c2b7", "muted": "#8a8880",
             "grid": "#2f2f2c", "surface": "rgba(0,0,0,0)", "axis": "#4a4a46",
             "surface_solid": "#1a1a19"},
}

# Status colors are reserved — never reused as a series hue.
STATUS = {"up": "#1baf7a", "down": "#e34948", "flat": "#8a8880"}


def active_mode() -> str:
    """Follow the viewer's Streamlit theme so dark mode is a real palette, not a flip."""
    try:
        import streamlit as st
        theme = getattr(st.context, "theme", None)
        if theme is not None and getattr(theme, "type", None) == "dark":
            return "dark"
    except Exception:
        pass
    return "light"


def palette(n: int | None = None, mode: str | None = None) -> list[str]:
    mode = mode or active_mode()
    colors = CATEGORICAL[mode]
    return colors if n is None else colors[:n]


def ink(mode: str | None = None) -> dict:
    return INK[mode or active_mode()]


def style(fig: go.Figure, *, height: int = 380, showlegend: bool | None = None,
          mode: str | None = None, y_title: str = "", x_title: str = "") -> go.Figure:
    """Apply the house layout: recessive grid, transparent surface, text ink."""
    mode = mode or active_mode()
    c = INK[mode]
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=32, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif",
                  size=13, color=c["secondary"]),
        hoverlabel=dict(font_size=12, namelength=-1),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(color=c["secondary"]), title_text=""),
        colorway=CATEGORICAL[mode],
    )
    if showlegend is not None:
        fig.update_layout(showlegend=showlegend)
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=c["axis"],
                     ticks="outside", tickcolor=c["axis"], tickfont=dict(color=c["muted"]),
                     title_text=x_title, title_font=dict(color=c["muted"], size=12))
    fig.update_yaxes(showgrid=True, gridcolor=c["grid"], gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", tickfont=dict(color=c["muted"]),
                     title_text=y_title, title_font=dict(color=c["muted"], size=12))
    return fig


def timeseries(frames: list[dict], *, height: int = 380, y_title: str = "Pageviews",
               mode: str | None = None) -> go.Figure:
    """Line chart. `frames` is [{'x':…, 'y':…, 'name':…, optional 'color','dash','width'}].

    A single series gets no legend box — the title names it.
    """
    mode = mode or active_mode()
    colors = CATEGORICAL[mode]
    fig = go.Figure()
    for i, f in enumerate(frames):
        fig.add_trace(go.Scatter(
            x=f["x"], y=f["y"], name=f.get("name", f"Series {i+1}"),
            mode="lines",
            line=dict(color=f.get("color", colors[i % len(colors)]),
                      width=f.get("width", 2), dash=f.get("dash")),
            fill=f.get("fill"),
            fillcolor=f.get("fillcolor"),
            hovertemplate=f.get("hovertemplate", "%{y:,.0f}<extra>%{fullData.name}</extra>"),
            opacity=f.get("opacity", 1.0),
            showlegend=f.get("showlegend", True),
        ))
    style(fig, height=height, showlegend=len(frames) > 1, mode=mode, y_title=y_title)
    return fig


def hbar(labels, values, *, height: int = 380, color: str | None = None,
         mode: str | None = None, text: list | None = None, x_title: str = "") -> go.Figure:
    """Horizontal ranking bars — 4px rounded data-ends, values labelled directly."""
    mode = mode or active_mode()
    c = INK[mode]
    fig = go.Figure(go.Bar(
        x=list(values), y=list(labels), orientation="h",
        marker=dict(color=color or CATEGORICAL[mode][0], cornerradius=4,
                    line=dict(width=0)),
        text=text, textposition="outside",
        textfont=dict(color=c["secondary"], size=12),
        cliponaxis=False,
        hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>",
    ))
    style(fig, height=height, showlegend=False, mode=mode, x_title=x_title)
    fig.update_layout(hovermode="closest", bargap=0.35)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    # Outside labels need room, or the longest bar's value is clipped at the edge.
    span = max([abs(float(v)) for v in values if v == v] or [1.0])
    fig.update_xaxes(showgrid=True, gridcolor=c["grid"], range=[0, span * 1.18])
    return fig


def network(nodes, edges, *, height: int = 620, mode: str | None = None,
            label_top: int = 12, highlight: set | None = None) -> go.Figure:
    """Attention map: one line trace for all edges, one scatter per community.

    Spatial proximity carries group identity as much as colour does — the layout
    puts a community's members next to each other — and the biggest nodes are
    directly labelled, which is the relief the light palette requires.
    """
    mode = mode or active_mode()
    c = INK[mode]
    colors = CATEGORICAL[mode]
    fig = go.Figure()

    if len(edges):
        pos = nodes.set_index("article_id")[["x", "y"]]
        xs, ys = [], []
        for row in edges.itertuples(index=False):
            if row.source_id in pos.index and row.target_id in pos.index:
                x0, y0 = pos.loc[row.source_id]
                x1, y1 = pos.loc[row.target_id]
                xs += [x0, x1, None]
                ys += [y0, y1, None]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", hoverinfo="skip", showlegend=False,
            line=dict(color=c["axis"], width=0.6), opacity=0.45,
        ))

    # Area-ish scaling on the percentile rank: big articles read as big without
    # a few giants swallowing their neighbours.
    sizes = nodes["views"].fillna(0)
    scaled = 6 + 20 * (sizes.rank(pct=True) ** 2) if len(sizes) else sizes

    for group, part in nodes.groupby("group_label", sort=False):
        idx = part["group_rank"].iloc[0]
        color = OTHER[mode] if idx < 0 else colors[idx % len(colors)]
        # A 2px surface ring keeps overlapping nodes readable as separate marks.
        marker = dict(size=scaled.loc[part.index], color=color,
                      line=dict(width=1.2, color=INK[mode]["surface_solid"]))
        if highlight:
            marker["opacity"] = [1.0 if a in highlight else 0.25
                                 for a in part["article_id"]]
        fig.add_trace(go.Scatter(
            x=part["x"], y=part["y"], mode="markers", name=group,
            marker=marker,
            customdata=part[["title", "views", "degree"]].to_numpy(),
            hovertemplate=("<b>%{customdata[0]}</b><br>%{customdata[1]:,.0f} views"
                           "<br>%{customdata[2]} connections<extra>" + group + "</extra>"),
        ))

    if label_top:
        # Label the biggest node in each group first, then the largest overall —
        # every coloured group gets named before any group gets a second label.
        leaders = nodes.loc[nodes.groupby("group_label")["views"].idxmax()]
        rest = nodes.drop(index=leaders.index).nlargest(
            max(0, label_top - len(leaders)), "views")
        top = pd.concat([leaders, rest]).head(label_top)
        fig.add_trace(go.Scatter(
            x=top["x"], y=top["y"], mode="text",
            text=[t.replace("_", " ") for t in top["title"]],
            textposition="top center", showlegend=False, hoverinfo="skip",
            textfont=dict(size=11, color=c["primary"]),
        ))

    style(fig, height=height, mode=mode)
    fig.update_layout(hovermode="closest",
                      legend=dict(orientation="h", y=-0.04, yanchor="top", x=0))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    return fig


def heatmap(matrix, *, height: int = 520, mode: str | None = None,
            labels: list | None = None, zmin: float = -1, zmax: float = 1) -> go.Figure:
    """Correlation matrix — diverging blue/red with a neutral gray midpoint."""
    mode = mode or active_mode()
    labels = labels if labels is not None else matrix.columns
    fig = go.Figure(go.Heatmap(
        z=matrix.to_numpy(), x=labels, y=labels,
        colorscale=DIVERGING, zmid=0, zmin=zmin, zmax=zmax,
        colorbar=dict(title="r", thickness=12, len=0.7,
                      tickfont=dict(color=INK[mode]["muted"], size=11)),
        hovertemplate="%{y}<br>%{x}<br>r = %{z:.2f}<extra></extra>",
        xgap=1, ygap=1,
    ))
    style(fig, height=height, showlegend=False, mode=mode)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(tickangle=-40, showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig
