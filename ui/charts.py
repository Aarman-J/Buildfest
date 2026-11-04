"""Plotly figure builders. Inputs are DataFrames from ui.transforms."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ui import theme as T


def _empty(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font={"color": T.MUTED})
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return T.layout(fig, height=160, legend=False)


def _err(df: pd.DataFrame) -> dict:
    return {"array": (df["ci_high"] - df["pct"]).tolist(), "arrayminus": (df["pct"] - df["ci_low"]).tolist(),
            "color": "#4B5563", "thickness": 1.2, "width": 4}


def before_after(df: pd.DataFrame, title: str, y: str = "label", height: int = 330) -> go.Figure:
    """Horizontal grouped bars: % agreeing before (recalled) vs after, with 95% CIs."""
    if df.empty:
        return _empty("No data available")
    fig = go.Figure()
    for phase in ("Before (recalled)", "After"):
        d = df[df["phase"] == phase]
        fig.add_bar(y=d[y], x=d["pct"], name=phase, orientation="h", marker_color=T.BEFORE_AFTER[phase],
                    error_x=_err(d), text=[f"{v:.1f}%" for v in d["pct"]], textposition="inside",
                    insidetextanchor="end", textfont={"color": "white" if phase == "After" else "#334155"},
                    customdata=d[["n"]].values,
                    hovertemplate="%{y}<br>" + phase + ": %{x:.1f}% agree<br>n = %{customdata[0]}<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.25)
    fig.update_xaxes(range=[0, 104], ticksuffix="%", title="% agreeing", tickvals=[0, 25, 50, 75, 100])
    fig.update_yaxes(autorange="reversed", title=None)
    return T.layout(fig, title, height)


def pp_change(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("No data available")
    fig = go.Figure(go.Bar(y=df["label"], x=df["change_pp"], orientation="h", marker_color=T.TEAL,
                           text=[f"{v:+.1f} pp" for v in df["change_pp"]], textposition="outside",
                           customdata=df[["n"]].values,
                           hovertemplate="%{y}<br>%{x:+.1f} percentage points<br>paired n = %{customdata[0]}<extra></extra>"))
    fig.update_xaxes(title="Change in % agreeing (percentage points)", range=[min(0, df["change_pp"].min() * 1.2),
                                                                            max(5, df["change_pp"].max() * 1.25)])
    fig.update_yaxes(autorange="reversed", title=None)
    return T.layout(fig, title, 260, legend=False)


def stacked_share(df: pd.DataFrame, title: str, cat: str, colors: dict, order: list[str], y: str = "label",
                  height: int = 280) -> go.Figure:
    if df.empty:
        return _empty("No data available")
    fig = go.Figure()
    for c in order:
        d = df[df[cat] == c]
        if d.empty:
            continue
        fig.add_bar(y=d[y], x=d["pct"], name=c, orientation="h", marker_color=colors.get(c, T.GREY),
                    text=[f"{v:.0f}%" if v >= 6 else "" for v in d["pct"]], textposition="inside",
                    textfont={"color": "white" if c not in ("Unchanged", "Neither agree nor disagree") else T.TEXT},
                    customdata=d[["k", "n"]].values if "k" in d else d[["n", "n"]].values,
                    hovertemplate="%{y}<br>" + c + ": %{x:.1f}% (%{customdata[0]} of %{customdata[1]})<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(ticksuffix="%", title=None)
    fig.update_yaxes(autorange="reversed", title=None)
    return T.layout(fig, title, height)


def transition_heatmap(pivot: pd.DataFrame, title: str) -> go.Figure:
    total = pivot.values.sum()
    pct = (pivot / total * 100).round(1)
    fig = go.Figure(go.Heatmap(z=pivot.values, x=pivot.columns, y=pivot.index, colorscale=[[0, "#F5F7FA"], [1, T.NAVY]],
                               text=[[f"{n}<br>({p}%)" for n, p in zip(r1, r2)] for r1, r2 in zip(pivot.values, pct.values)],
                               texttemplate="%{text}", hovertemplate="Before: %{y}<br>After: %{x}<br>n = %{z}<extra></extra>",
                               showscale=False))
    fig.update_xaxes(title="After training", side="bottom")
    fig.update_yaxes(title="Before (recalled)", autorange="reversed")
    return T.layout(fig, title, 420, legend=False)


def trend_lines(df: pd.DataFrame, metric: str, title: str, ytitle: str) -> go.Figure:
    d = df[~df["suppressed"]]
    if d.empty:
        return _empty("No months with enough responses")
    fig = go.Figure()
    for i, (lab, g) in enumerate(d.groupby("label", sort=False)):
        g = g.sort_values("month")
        fig.add_scatter(x=g["month"], y=g[metric], mode="lines+markers", name=lab,
                        line={"color": T.SEQUENCE[i % len(T.SEQUENCE)], "width": 2},
                        marker={"size": [7 if not low else 11 for low in g["low_n"]],
                                "symbol": ["circle" if not low else "circle-open" for low in g["low_n"]]},
                        customdata=g[["paired_n", "n_sessions"]].values,
                        hovertemplate="%{x}<br>" + lab + ": %{y:.1f}<br>paired n = %{customdata[0]}, "
                                      "sessions = %{customdata[1]}<extra></extra>")
    fig.update_xaxes(type="category", title="Training month")
    fig.update_yaxes(title=ytitle)
    return T.layout(fig, title, 360)


def month_counts(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("No dated responses")
    colors = [T.LIGHT if s else (T.GOLD if low else T.NAVY) for s, low in zip(df["suppressed"], df["low_n"])]
    fig = go.Figure(go.Bar(x=df["month"], y=df["n_rows"], marker_color=colors, text=df["n_rows"],
                           textposition="outside", customdata=df[["n_sessions"]].values,
                           hovertemplate="%{x}<br>%{y} responses, %{customdata[0]} session(s)<extra></extra>"))
    fig.update_xaxes(type="category", title=None)
    fig.update_yaxes(title="Responses")
    return T.layout(fig, title, 240, legend=False)


def likert_stacked(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("Not collected in this dataset")
    d = df.rename(columns={"item": "label", "answer": "cat"})
    d["k"] = d["n"]
    d["n"] = d["valid_n"]
    return stacked_share(d, title, "cat", T.LIKERT, list(T.LIKERT), height=260)


def rating_bars(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("Not collected in this dataset")
    fig = go.Figure(go.Bar(x=df["score"], y=df["pct"], marker_color=[T.RATING[b] for b in df["band"]],
                           customdata=df[["n"]].values, text=[f"{v:.0f}%" for v in df["pct"]], textposition="outside",
                           hovertemplate="Score %{x}: %{y:.1f}% (n = %{customdata[0]})<extra></extra>"))
    fig.update_xaxes(tickmode="linear", dtick=1, title="Rating (0 = not at all likely, 10 = extremely likely)")
    fig.update_yaxes(ticksuffix="%", title="% of valid answers")
    return T.layout(fig, title, 300, legend=False)


def correlation_bars(df: pd.DataFrame, title: str) -> go.Figure:
    d = df.dropna(subset=["rho"])
    if d.empty:
        return _empty("Not enough paired data")
    fig = go.Figure(go.Bar(y=d["pair"], x=d["rho"], orientation="h",
                           marker_color=[T.NAVY if abs(r) >= 0.3 else T.GREY for r in d["rho"]],
                           text=[f"ρ={r:.2f}, n={n}" for r, n in zip(d["rho"], d["n"])], textposition="outside",
                           customdata=d[["n", "p_value", "strength"]].values,
                           hovertemplate="%{y}<br>Spearman ρ = %{x:.2f} (%{customdata[2]})<br>n = %{customdata[0]}, "
                                         "p = %{customdata[1]}<extra></extra>"))
    for v in (0.1, 0.3, 0.5):
        fig.add_vline(x=v, line_dash="dot", line_color="#CBD2DC")
    fig.update_xaxes(range=[min(-0.1, d["rho"].min() - 0.1), max(0.6, d["rho"].max() + 0.25)],
                     title="Spearman rank correlation (ρ)")
    fig.update_yaxes(autorange="reversed", title=None)
    return T.layout(fig, title, max(260, 34 * len(d) + 80), legend=False)


def grouped_share(df: pd.DataFrame, title: str, x: str, color: str, colors: dict | None = None,
                  height: int = 340) -> go.Figure:
    """Vertical grouped bars of a percentage, with 95% CIs when available."""
    if df.empty:
        return _empty("No groups large enough to show")
    fig = go.Figure()
    for i, (c, d) in enumerate(df.groupby(color, sort=False)):
        fig.add_bar(x=d[x], y=d["pct"], name=str(c),
                    marker_color=(colors or {}).get(c, T.SEQUENCE[i % len(T.SEQUENCE)]),
                    error_y=_err(d) if {"ci_low", "ci_high"} <= set(d.columns) else None,
                    customdata=d[["n"]].values, text=[f"{v:.0f}%" for v in d["pct"]], textposition="inside",
                    insidetextanchor="start",
                    textfont={"color": "#334155" if (colors or {}).get(c) in (T.SLATE, T.LIGHT) else "white",
                              "size": 13},
                    hovertemplate="%{x}<br>" + str(c) + ": %{y:.1f}% (n = %{customdata[0]})<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_yaxes(ticksuffix="%", range=[0, 110], title=None)
    fig.update_xaxes(title=None)
    return T.layout(fig, title, height)


def theme_bars(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("No comments in this dataset")
    d = df.sort_values("n", ascending=True)
    fig = go.Figure(go.Bar(y=d["label"], x=d["pct_of_comments"], orientation="h",
                           marker_color=[T.GREY if t == "other_unclassified" else T.TEAL for t in d["theme"]],
                           text=[f"{n} ({p:.0f}%)" for n, p in zip(d["n"], d["pct_of_comments"])], textposition="outside",
                           hovertemplate="%{y}: %{x:.1f}% of comments<extra></extra>"))
    fig.update_xaxes(ticksuffix="%", title="% of comments (a comment can have several themes)",
                     range=[0, d["pct_of_comments"].max() * 1.3 + 1])
    fig.update_yaxes(title=None)
    return T.layout(fig, title, max(260, 28 * len(d) + 80), legend=False)


def composition_dumbbell(df: pd.DataFrame, title: str) -> go.Figure:
    if df.empty:
        return _empty("Not enough respondents per role group to adjust")
    fig = px.scatter(df, x="pct", y="county", color="version", symbol="phase",
                     color_discrete_sequence=[T.NAVY, T.GOLD], hover_data={"n": True})
    fig.update_traces(marker={"size": 12})
    fig.update_xaxes(ticksuffix="%", title="% agreeing")
    fig.update_yaxes(title=None)
    return T.layout(fig, title, 300)
