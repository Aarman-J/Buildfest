"""Visual design system: colour-blind-safe palette (indigo / slate / orange), Plotly defaults and CSS."""

from __future__ import annotations

INDIGO = "#4F46E5"
INDIGO_DARK = "#3730A3"
INDIGO_LIGHT = "#A5B4FC"
TEAL = "#0D9488"
SLATE = "#CBD5E1"
SLATE_DARK = "#64748B"
ORANGE = "#EA580C"
AMBER = "#F59E0B"
INK = "#0F172A"
MUTED = "#64748B"
GRID = "#EEF1F6"

# Backwards-compatible names used across pages/charts
NAVY, GREY, LIGHT, GOLD, TEXT = INDIGO, SLATE, "#E2E8F0", AMBER, INK

BEFORE_AFTER = {"Before (recalled)": SLATE, "After": INDIGO}
CHANGE = {"Improved": INDIGO, "Unchanged": "#E2E8F0", "Declined": ORANGE}
GAP = {"Neutral after training": AMBER, "Disagree after training": ORANGE}
LIKERT = {
    "Strongly disagree": "#C2410C", "Disagree": "#FB923C", "Neither agree nor disagree": "#E2E8F0",
    "Agree": "#818CF8", "Strongly agree": INDIGO_DARK,
}
RATING = {"0-6": "#FB923C", "7-8": SLATE, "9-10": INDIGO}
AGREEMENT = {"Reached agreement": INDIGO, "Maintained agreement": "#A5B4FC",
             "Remained below agreement": AMBER, "Moved below agreement": ORANGE}
AUDIENCE = {"Students": INDIGO, "Staff, caregivers & community members": TEAL}
COMPARE = {"Confident after training": INDIGO, "Still not confident": ORANGE}
SEQUENCE = [INDIGO, TEAL, AMBER, ORANGE, "#8B5CF6", SLATE_DARK, "#0EA5E9"]

FONT = "Inter, Segoe UI, Helvetica, Arial, sans-serif"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {font-family: 'Inter', 'Segoe UI', sans-serif;}
.block-container {padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1280px;}
h1, h2, h3, h4 {color: #0F172A; letter-spacing: -0.02em;}

/* ---------- sidebar ---------- */
section[data-testid="stSidebar"] {background: linear-gradient(180deg, #0F172A 0%, #1E1B4B 100%);}
section[data-testid="stSidebar"] * {color: #E2E8F0;}
section[data-testid="stSidebar"] code {background: rgba(255,255,255,.08); color: #C7D2FE;}
section[data-testid="stSidebar"] hr {border-color: rgba(255,255,255,.08);}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] {gap: 3px; width: 100%;}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] > div {width: 100%;}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] {
    width: 100%; padding: .6rem .8rem; border-radius: 10px; margin: 0; transition: background .15s; cursor: pointer;}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {background: rgba(255,255,255,.07);}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] {
    background: linear-gradient(90deg, rgba(99,102,241,.45), rgba(99,102,241,.12));
    box-shadow: inset 3px 0 0 #A5B4FC;}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child {display: none;}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"] p {
    font-size: .92rem; font-weight: 500; white-space: nowrap; margin: 0;}
section[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] p {font-weight: 700; color: #FFFFFF;}
header[data-testid="stHeader"] {background: transparent;}
.em-brand {display:flex; align-items:center; gap:.65rem; margin:.2rem 0 1rem 0;}
.em-brand .logo {width:38px; height:38px; border-radius:11px; display:flex; align-items:center; justify-content:center;
    background: linear-gradient(135deg,#6366F1,#14B8A6); font-weight:800; color:white !important; font-size:1.05rem;}
.em-brand .name {font-weight:800; font-size:1.05rem; line-height:1.1; color:#FFFFFF !important;}
.em-brand .sub {font-size:.74rem; color:#A5B4FC !important;}
.em-side-card {background: rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08); border-radius:12px;
    padding:.75rem .85rem; font-size:.82rem;}
.em-side-card b {color:#FFFFFF !important;}
.em-dot {display:inline-block; width:8px; height:8px; border-radius:50%; background:#22C55E; margin-right:.4rem;
    box-shadow:0 0 0 3px rgba(34,197,94,.2);}

/* ---------- hero & headers ---------- */
.em-hero {position:relative; overflow:hidden; border-radius:22px; padding:2rem 2.2rem; color:white; margin-bottom:1.2rem;
    background: radial-gradient(1200px 300px at 90% -20%, rgba(20,184,166,.55), transparent 60%),
                linear-gradient(120deg, #1E1B4B 0%, #3730A3 45%, #4F46E5 100%);
    box-shadow: 0 18px 40px -18px rgba(55,48,163,.55);}
.em-hero .eyebrow {text-transform:uppercase; letter-spacing:.14em; font-size:.72rem; font-weight:700; color:#C7D2FE;}
.em-hero h1 {color:white; font-size:2.1rem; margin:.35rem 0 .5rem 0; line-height:1.15;}
.em-hero p {color:#E0E7FF; font-size:1.02rem; max-width:860px; margin:0;}
.em-chips {margin-top:1rem; display:flex; gap:.5rem; flex-wrap:wrap;}
.em-chip {background: rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.22); color:white; border-radius:999px;
    padding:.28rem .75rem; font-size:.8rem; font-weight:600; backdrop-filter: blur(4px);}
.em-header {margin: .2rem 0 1rem 0;}
.em-header .eyebrow {text-transform:uppercase; letter-spacing:.14em; font-size:.72rem; font-weight:700; color:#4F46E5;}
.em-header h1 {font-size:2rem; margin:.2rem 0 .2rem 0;}
.em-question {background:white; border:1px solid #E3E7EF; border-left:4px solid #4F46E5; padding:.85rem 1.1rem;
    border-radius:12px; margin:.2rem 0 1rem 0; color:#334155; box-shadow:0 1px 2px rgba(15,23,42,.04);}
.em-question .em-label {font-size:.72rem; text-transform:uppercase; letter-spacing:.1em; color:#4F46E5; font-weight:700;
    margin-bottom:.15rem;}
.em-section {display:flex; align-items:baseline; gap:.6rem; margin:1.6rem 0 .7rem 0;}
.em-section .h {margin:0; font-size:1.35rem; font-weight:800; color:#0F172A; letter-spacing:-0.02em;}
.em-section span {color:#64748B; font-size:.9rem;}

/* ---------- cards ---------- */
.em-kpi {background:white; border:1px solid #E3E7EF; border-radius:16px; padding:1rem 1.1rem; height:100%;
    box-shadow:0 1px 2px rgba(15,23,42,.04), 0 8px 24px -16px rgba(15,23,42,.18);}
.em-kpi .top {display:flex; align-items:center; justify-content:space-between;}
.em-kpi .ico {width:34px; height:34px; border-radius:10px; display:flex; align-items:center; justify-content:center;
    font-size:1.05rem; background:#EEF2FF;}
.em-kpi .v {font-size:1.9rem; font-weight:800; color:#0F172A; line-height:1.1; margin-top:.55rem; letter-spacing:-0.02em;}
.em-kpi .l {font-size:.86rem; color:#334155; font-weight:600; margin-top:.15rem;}
.em-kpi .s {font-size:.76rem; color:#64748B; margin-top:.25rem;}
.em-card {background:white; border:1px solid #E3E7EF; border-radius:16px; padding:1.1rem 1.2rem; height:100%;
    box-shadow:0 1px 2px rgba(15,23,42,.04), 0 8px 24px -16px rgba(15,23,42,.18);}
.em-card h4 {margin:0 0 .4rem 0; font-size:1rem;}
.em-card p, .em-card li {color:#334155; font-size:.9rem;}
.em-obj .name {font-weight:700; color:#0F172A; font-size:.95rem; min-height:2.6em;}
.em-obj .nums {display:flex; align-items:baseline; gap:.5rem; margin:.5rem 0 .6rem 0;}
.em-obj .before {font-size:1.25rem; font-weight:700; color:#94A3B8;}
.em-obj .arrow {color:#94A3B8;}
.em-obj .after {font-size:2rem; font-weight:800; color:#4F46E5; letter-spacing:-0.02em;}
.em-bar {height:8px; background:#EEF1F6; border-radius:999px; overflow:hidden; margin:.25rem 0 .2rem 0;}
.em-bar > div {height:100%; border-radius:999px;}
.em-bar-label {display:flex; justify-content:space-between; font-size:.74rem; color:#64748B;}
.em-pill {display:inline-block; font-size:.74rem; font-weight:700; padding:.18rem .55rem; border-radius:999px;}
.em-pill.up {background:#EEF2FF; color:#3730A3;}
.em-pill.warn {background:#FFF7ED; color:#C2410C;}
.em-tag {display:inline-block; font-size:.7rem; font-weight:700; padding:.15rem .55rem; border-radius:999px;
    margin-right:.35rem; letter-spacing:.02em;}
.em-tag.high {background:#FEE2E2; color:#B91C1C;}
.em-tag.medium {background:#FEF3C7; color:#92400E;}
.em-tag.low {background:#F1F5F9; color:#334155;}
.em-tag.cat {background:#EEF2FF; color:#3730A3;}
.em-tag.ai {background:#F3E8FF; color:#6B21A8;}
.em-rec {background:white; border:1px solid #E3E7EF; border-radius:16px; padding:1rem 1.15rem; height:100%;
    border-top:4px solid #4F46E5; box-shadow:0 8px 24px -16px rgba(15,23,42,.18);}
.em-rec.high {border-top-color:#DC2626;} .em-rec.medium {border-top-color:#F59E0B;} .em-rec.low {border-top-color:#94A3B8;}
.em-rec .t {font-weight:700; color:#0F172A; margin:.45rem 0 .35rem 0; font-size:1rem;}
.em-rec .f {color:#334155; font-size:.88rem; margin-bottom:.5rem;}
.em-rec .row {font-size:.84rem; color:#475569; margin:.2rem 0;}
.em-rec .row b {color:#0F172A;}
.em-small {font-size:.82rem; color:#64748B;}
.em-insight {display:flex; gap:.75rem; align-items:flex-start;}
.em-insight .ico {font-size:1.3rem; line-height:1;}

/* charts & tables sit on white cards */
div[data-testid="stPlotlyChart"] {background:white; border:1px solid #E3E7EF; border-radius:16px; padding:.6rem .7rem;
    box-shadow:0 8px 24px -18px rgba(15,23,42,.18);}
div[data-testid="stTabs"] button[role="tab"] p {font-weight:600;}
div[data-testid="stExpander"] details {background:white; border-radius:12px;}
.em-takeaway {font-size:.86rem; color:#475569; margin:-.2rem 0 1rem .2rem;}
.em-takeaway b {color:#0F172A;}

/* small screens (phones) */
@media (max-width: 640px) {
  .block-container {padding-left: .8rem; padding-right: .8rem; padding-top: 2.6rem;}
  .em-hero {padding: 1.2rem 1.1rem; border-radius: 16px;}
  .em-hero h1 {font-size: 1.35rem;}
  .em-hero p {font-size: .9rem;}
  .em-kpi .v {font-size: 1.45rem;}
  .em-obj .after {font-size: 1.5rem;}
  .em-header h1 {font-size: 1.5rem;}
}
</style>
"""


def layout(fig, title: str | None = None, height: int = 380, legend: bool = True):
    """Apply consistent Plotly styling."""
    fig.update_layout(
        title={"text": f"<b>{title}</b>" if title else None, "x": 0.01, "y": 0.98, "yanchor": "top",
               "font": {"size": 15, "color": INK}},
        height=height, font={"family": FONT, "size": 13, "color": "#334155"},
        margin={"l": 8, "r": 24, "t": 64 if title else 28, "b": 36},
        plot_bgcolor="white", paper_bgcolor="white", showlegend=legend,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "left", "x": 0, "title": None,
                "font": {"size": 12}},
        hoverlabel={"font": {"family": FONT}, "bgcolor": "white", "bordercolor": "#E3E7EF"},
        bargap=0.28,
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, linecolor="#E3E7EF")
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, linecolor="#E3E7EF")
    return fig
