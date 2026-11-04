"""Exploratory data analysis and schema discovery for EveryMind 988 training surveys.

Read-only: the input file is never modified (verified with a SHA-256 checksum
before and after). No cleaning, imputation, or row removal happens here; the
script only measures and reports.

Usage:
    python src/explore_data.py
    python src/explore_data.py --input path/to/new_survey.xlsx --outdir outputs

Outputs:
    <outdir>/data_profile.md       findings report
    <outdir>/data_dictionary.csv   one row per original column
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
DEFAULT_OUTDIR = PROJECT_ROOT / "outputs"

# ---------------------------------------------------------------------------
# Canonical schema and text helpers: shared with the cleaning pipeline
# ---------------------------------------------------------------------------

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schema_mapper import (  # noqa: E402
    AGREE_5, LIKERT_SYNONYMS, PRE_POST_PAIRS, SAT_FIELDS, SCHEMA, SCHEMA_BY_NAME, TEXT_FIELDS, LIKERT_FIELDS,
    has_hidden_chars, match_columns, norm_text,
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_workbook(path: Path) -> dict[str, pd.DataFrame]:
    """Return {sheet_name: DataFrame}. CSVs become a single pseudo-sheet."""
    if path.suffix.lower() == ".csv":
        return {path.stem: pd.read_csv(path, dtype=object, keep_default_na=True)}
    # dtype=object keeps raw cell values; pandas would otherwise coerce labels.
    return pd.read_excel(path, sheet_name=None, dtype=object)


def sheet_match_score(df: pd.DataFrame) -> int:
    return sum(1 for m in match_columns(df.columns).values() if m["field"])


def pick_survey_sheet(sheets: dict[str, pd.DataFrame]) -> str:
    """Choose the sheet whose headers match the most canonical fields, tie-break on rows."""
    return max(sheets, key=lambda s: (sheet_match_score(sheets[s]), len(sheets[s])))


# ---------------------------------------------------------------------------
# Profiling helpers
# ---------------------------------------------------------------------------

def pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.1f}%" if d else "n/a"


def vc_table(s: pd.Series, top: int | None = None, show_repr: bool = True, sort_index: bool = False) -> str:
    vc = s.value_counts(dropna=False)
    if sort_index:
        vc = vc.sort_index()
    if top:
        vc = vc.head(top)
    total = len(s)
    lines = ["| Value | Count | % |", "|---|---:|---:|"]
    for v, n in vc.items():
        label = "(missing)" if pd.isna(v) else (repr(v) if show_repr else str(v))
        label = label.replace("|", "\\|")
        lines.append(f"| `{label}` | {n} | {pct(n, total)} |")
    return "\n".join(lines)


def md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        out.append("| " + " | ".join(str(v).replace("|", "\\|") for v in row.values) + " |")
    return "\n".join(out)


def label_variants(s: pd.Series) -> dict[str, list[str]]:
    """Group raw labels that collapse to the same normalized form."""
    groups: dict[str, set[str]] = {}
    for v in s.dropna().unique():
        groups.setdefault(norm_text(v), set()).add(v)
    return {k: sorted(v, key=repr) for k, v in groups.items() if len(v) > 1}


def county_key(v: object) -> str:
    s = norm_text(v)
    s = re.sub(r"\bcounty\b", "", s)
    s = re.sub(r"\bwith\b.*$", "", s)  # e.g. "Montgomery County with NAMI MC"
    return re.sub(r"\s+", " ", s).strip()


def split_roles(v: object) -> list[str]:
    if pd.isna(v):
        return []
    return [t.strip() for t in str(v).split(";") if t.strip()]


PII_PATTERNS = {
    "email address": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "phone-like number (10 digits)": r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b",
    "URL": r"https?://|www\.",
}

NON_ANSWERS = {"n/a", "na", "none", "nothing", "no", ".", "-", "idk", "nope", "none.", "nothing.", "n/a.", "no.",
               "nothing really", "nothing at all", "not sure", "no comment", "no comments", "all good", "nothing!"}


# ---------------------------------------------------------------------------
# Main profile
# ---------------------------------------------------------------------------

def profile(input_path: Path, outdir: Path) -> None:
    checksum_before = sha256(input_path)
    sheets = load_workbook(input_path)
    sheet = pick_survey_sheet(sheets)
    raw = sheets[sheet]
    n_rows, n_cols = raw.shape

    mapping = match_columns(raw.columns)
    col_of = {m["field"]: col for col, m in mapping.items() if m["field"]}
    missing_required = [f.name for f in SCHEMA if f.required and f.name not in col_of]
    missing_optional = [f.name for f in SCHEMA if not f.required and f.name not in col_of]
    unmapped_cols = [c for c, m in mapping.items() if not m["field"]]

    def col(name: str) -> pd.Series | None:
        return raw[col_of[name]] if name in col_of else None

    lang = col("language")

    r: list[str] = []  # report lines
    add = r.append

    add("# EveryMind 988 Training Survey: Data Profile\n")
    add(f"_Generated by `src/explore_data.py` on {datetime.now():%Y-%m-%d %H:%M}._\n")
    add(f"- **Input file:** `{input_path.name}`")
    add(f"- **SHA-256:** `{checksum_before}`")
    add("- **Scope:** read-only exploration. No rows were removed, no values were changed or imputed.")
    add("- **Privacy:** free-text responses are summarized by counts and lengths only; no comments are quoted.\n")

    # 1. Structure ----------------------------------------------------------
    add("## 1. Dataset structure\n")
    add("| Worksheet | Rows | Columns | Canonical fields matched |")
    add("|---|---:|---:|---:|")
    for s, d in sheets.items():
        add(f"| {s}{' (selected)' if s == sheet else ''} | {len(d)} | {d.shape[1]} | {sheet_match_score(d)} |")
    add(f"\nThe survey sheet (**{sheet}**) was chosen automatically as the sheet that matches the most canonical "
        f"fields. It has **{n_rows} rows** (one per response) and **{n_cols} columns**.\n")

    add("### Columns, types and canonical mapping\n")
    rows = []
    for i, c in enumerate(raw.columns):
        m = mapping[c]
        f = SCHEMA_BY_NAME.get(m["field"]) if m["field"] else None
        rows.append({
            "#": i,
            "Original header (normalized)": norm_text(c)[:90] + ("..." if len(norm_text(c)) > 90 else ""),
            "Pandas type (raw)": pd.api.types.infer_dtype(raw[c], skipna=True),
            "Canonical field": m["field"] or f"_{m['confidence']}_",
            "Category": f.category if f else "",
            "Match": m["method"],
        })
    add(md_table(pd.DataFrame(rows)))
    hidden = [i for i, c in enumerate(raw.columns) if has_hidden_chars(c)]
    add(f"\n{len(hidden)} of {n_cols} headers contain non-breaking spaces, invisible marks or trailing/double "
        f"spaces (columns {hidden}). Headers must be normalized before matching.\n")
    if missing_required:
        add(f"**Required canonical fields not found:** {', '.join(missing_required)}\n")
    if missing_optional:
        add(f"Optional canonical fields not found: {', '.join(missing_optional)}\n")
    if unmapped_cols:
        add(f"Unmapped original columns: {[norm_text(c)[:60] for c in unmapped_cols]}\n")

    add("### Column classification\n")
    for cat in ["demographics", "training_outcomes", "training_experience", "behavioral_intent",
                "written_feedback", "metadata"]:
        names = [f.name for f in SCHEMA if f.category == cat and f.name in col_of]
        add(f"- **{cat.replace('_', ' ').title()}:** {', '.join(names) or '(none)'}")
    add("")

    # 2. Data quality --------------------------------------------------------
    add("## 2. Data quality assessment\n")
    add("### 2.1 Missing values\n")
    miss = pd.DataFrame({
        "Canonical field": [mapping[c]["field"] or "(unmapped)" for c in raw.columns],
        "Non-missing": raw.notna().sum().values,
        "Missing": raw.isna().sum().values,
        "Missing %": [pct(n, n_rows) for n in raw.isna().sum().values],
    })
    add(md_table(miss))
    blank_str = {mapping[c]["field"] or c: int(raw[c].astype(str).str.strip().eq("").sum())
                 for c in raw.columns if raw[c].dtype == object}
    blank_str = {k: v for k, v in blank_str.items() if v}
    add(f"\nEmpty or whitespace-only strings that pandas does not count as missing: {blank_str or 'none'}.\n")

    add("### 2.2 Duplicates\n")
    content_cols = [c for c in raw.columns if mapping[c]["field"] not in ("response_id", "start_time", "completion_time")]
    id_s = col("response_id")
    add(f"- Fully identical rows: **{int(raw.duplicated().sum())}**")
    if id_s is not None:
        add(f"- Duplicate response IDs: **{int(id_s.duplicated().sum())}** "
            f"(IDs range {id_s.min()} to {id_s.max()}; {id_s.nunique()} unique)")
    add(f"- Rows with identical answers on every non-ID, non-timestamp column: "
        f"**{int(raw[content_cols].duplicated(keep=False).sum())}**")
    st = col("start_time")
    if st is not None:
        dup_start = int(st.duplicated(keep=False).sum())
        add(f"- Rows sharing an exact start timestamp with another row: **{dup_start}**. This is expected in "
            "group sessions where people open the survey together, so it is not evidence of duplication on its own.")
    add("")

    # 2.3 Categorical values
    add("### 2.3 Categorical responses and label consistency\n")
    for name in ["language", "participant_role", "county"]:
        s = col(name)
        if s is None:
            continue
        add(f"#### {name}\n")
        add(vc_table(s))
        add("")
        variants = label_variants(s)
        if variants:
            add("Raw labels that are identical after whitespace/case normalization:\n")
            for k, v in variants.items():
                add(f"- `{k}` <- {', '.join(f'`{x!r}`' for x in v)}")
            add("")

    if lang is not None:
        hidden_lang = int(lang.dropna().map(lambda v: "‎" in str(v)).sum())
        add(f"Language: {hidden_lang} of {lang.notna().sum()} values end in an invisible left-to-right mark "
            "(U+200E), which the form platform adds. Strip it before grouping.\n")

    roles = col("participant_role")
    if roles is not None:
        tokens = roles.map(split_roles)
        flat = pd.Series([t for ts in tokens for t in ts])
        multi = int((tokens.map(len) > 1).sum())
        trailing = int(roles.dropna().astype(str).str.endswith(";").sum())
        add("**Participant role tokens** (split on `;`). Respondents may select more than one role, so the "
            "counts below add up to more than the number of rows:\n")
        add(vc_table(flat))
        add(f"\n- {multi} rows ({pct(multi, n_rows)}) select more than one role.")
        add(f"- {trailing} rows ({pct(trailing, n_rows)}) end in a trailing `;`. The same answer appears both "
            "with and without it (e.g. `Caregiver/parent` vs `Caregiver/parent;`), which looks like an export "
            "difference between form versions rather than a different answer.")
        add(f"- Token order varies (`Caregiver/parent;Student` vs `Student;Caregiver/parent;`); sort or "
            "one-hot encode tokens.\n")

    county = col("county")
    if county is not None:
        keys = county.map(county_key)
        add("**County names grouped by normalized key** (case, whitespace, NBSP and the word 'County' "
            "removed; text after 'with' dropped):\n")
        grp = (pd.DataFrame({"key": keys, "raw": county.map(repr)})
               .groupby("key")
               .agg(rows=("raw", "size"), raw_variants=("raw", lambda x: ", ".join(sorted(set(x)))))
               .sort_values("rows", ascending=False).reset_index())
        add(md_table(grp))
        add(f"\n{county.nunique()} raw labels collapse to {keys.nunique()} counties. Notes:")
        add("- `Fairfax` (Virginia) and `Harris` (Texas) are not Maryland counties. They may be real out-of-state "
            "sessions or entry errors, so they need confirmation from EveryMind.")
        add("- `Montgomery County with NAMI MC` adds a partner organization to the county name. Keep the partner "
            "as a separate attribute rather than dropping it.\n")

    # Likert
    add("#### Likert scales\n")
    lik_rows = []
    for name in LIKERT_FIELDS:
        s = col(name)
        if s is None:
            continue
        f = SCHEMA_BY_NAME[name]
        norm_allowed = {norm_text(a) for a in AGREE_5}
        vals = s.dropna()
        is_syn = vals.map(norm_text).isin(LIKERT_SYNONYMS)
        off = vals[~vals.map(norm_text).isin(norm_allowed) & ~is_syn]
        lik_rows.append({
            "Field": name,
            "Distinct raw labels": s.nunique(),
            "Labels w/ hidden chars": int(vals.map(has_hidden_chars).sum()),
            "Wording variants": int(is_syn.sum()),
            "Off-scale values": len(off),
            "Off-scale labels": "; ".join(f"{v!r} ({n})" for v, n in off.value_counts().items()) or "-",
        })
    add(md_table(pd.DataFrame(lik_rows)))
    add("\nNotes:")
    add("- The outcome items (pre/post) use `Neither agree nor disagree`. The training-experience items use "
        "`Neither agree or disagree` and `Strongly agree\\xa0` (with a trailing non-breaking space). This points "
        "to a separately built question block.")
    add("- The training-experience items also contain **`Very agree`** and **`Satisfied`**, which are not on the "
        "agree/disagree scale. They appear to come from another form version (see 2.6). Mapping them to "
        "`Strongly agree`/`Agree` is an assumption and needs confirmation.\n")
    sat_cols = [col(n) for n in SAT_FIELDS if col(n) is not None]
    if sat_cols:
        odd = pd.concat(sat_cols, axis=1).isin(["Very agree", "Satisfied"]).any(axis=1)
        spanish = lang.map(norm_text).str.startswith("espa") if lang is not None else False
        add(f"Rows with any `Very agree`/`Satisfied` label: {int(odd.sum())}; of these, "
            f"{int((odd & spanish).sum())} are Spanish-language responses.\n")

    for name in SAT_FIELDS[:1]:
        s = col(name)
        if s is not None:
            add(f"Example full distribution ({name}):\n")
            add(vc_table(s))
            add("")

    # Numeric 0-10
    add("#### 0-10 rating items\n")
    for name in ["likelihood_recommend", "likelihood_call_988"]:
        s = col(name)
        if s is None:
            continue
        num = pd.to_numeric(s, errors="coerce")
        nonnum = int((s.notna() & num.isna()).sum())
        out_rng = int(((num < 0) | (num > 10)).sum())
        nonint = int((num.notna() & (num % 1 != 0)).sum())
        add(f"- **{name}**: {num.notna().sum()} numeric answers ({pct(int(num.notna().sum()), n_rows)}), "
            f"{int(s.isna().sum())} missing, {nonnum} non-numeric, {out_rng} outside 0-10, {nonint} non-integers. "
            f"Median {num.median():.0f}, mean {num.mean():.2f}.")
    add("")

    # 2.4 Dates
    add("### 2.4 Dates and timestamps\n")
    ct = col("completion_time")
    td = col("training_date")
    st_dt = pd.to_datetime(st, errors="coerce") if st is not None else None
    ct_dt = pd.to_datetime(ct, errors="coerce") if ct is not None else None
    if st is not None:
        add(f"- start_time: {int(st.notna().sum())} values, {int((st.notna() & st_dt.isna()).sum())} unparseable, "
            f"range {st_dt.min()} to {st_dt.max()}. Raw format example: `{st.dropna().iloc[0]}`.")
    if ct is not None:
        add(f"- completion_time: {int(ct.notna().sum())} values, {int((ct.notna() & ct_dt.isna()).sum())} "
            f"unparseable, range {ct_dt.min()} to {ct_dt.max()}.")
    if st is not None and ct is not None:
        dur = (ct_dt - st_dt).dt.total_seconds()
        add(f"- Completion duration (seconds): median {dur.median():.0f}, IQR {dur.quantile(.25):.0f}-"
            f"{dur.quantile(.75):.0f}, min {dur.min():.0f}, max {dur.max():.0f}. Negative: "
            f"{int((dur < 0).sum())}; under 30 s: {int((dur < 30).sum())} ({pct(int((dur < 30).sum()), n_rows)}); "
            f"over 1 h: {int((dur > 3600).sum())}.")
    if td is not None:
        td_dt = pd.to_datetime(td, errors="coerce")
        fmt = td.dropna().astype(str).str.replace(r"\d", "9", regex=True).value_counts()
        add(f"- training_date: {int(td.notna().sum())} values, {int(td.isna().sum())} missing, "
            f"{int((td.notna() & td_dt.isna()).sum())} unparseable. Raw format patterns: "
            + ", ".join(f"`{k}` ({v})" for k, v in fmt.items()) + f". Range {td_dt.min():%Y-%m-%d} to "
            f"{td_dt.max():%Y-%m-%d}; {td_dt.nunique()} distinct dates.")
        ref = ct_dt if ct_dt is not None else st_dt
        if ref is not None:
            lag = (ref.dt.normalize() - td_dt).dt.days
            add(f"- Days from training date to survey completion: median {lag.median():.0f}, "
                f"IQR {lag.quantile(.25):.0f}-{lag.quantile(.75):.0f}, max {lag.max():.0f}. "
                f"Training date **after** completion: {int((lag < 0).sum())}; more than 30 days before: "
                f"{int((lag > 30).sum())}; more than 90 days: {int((lag > 90).sum())}.")
            add("- The training date is entered by the respondent and is usually several days before completion. "
                "That is plausible (surveys sent after the session), but it means the field can contain typing "
                "errors. Use completion_time as the fallback when it is missing or implausible.")
        add("")
        add("Responses per calendar month (by completion time):\n")
        if ct_dt is not None:
            add(vc_table(ct_dt.dt.to_period("M").astype(str), show_repr=False, sort_index=True)
                .replace("| Value |", "| Month |"))
        add("")

    # 2.5 Outliers / suspicious
    add("### 2.5 Outliers and suspicious response patterns (flagged, not removed)\n")
    post = [col(n) for n in ["post_identify_mh", "post_describe_988", "post_know_when_988"]]
    pre = [col(n) for n in ["pre_identify_mh", "pre_describe_988", "pre_know_when_988"]]
    if all(s is not None for s in post + pre):
        post_df = pd.concat(post, axis=1).apply(lambda c: c.map(norm_text, na_action="ignore"))
        pre_df = pd.concat(pre, axis=1).apply(lambda c: c.map(norm_text, na_action="ignore"))
        both = pd.concat([pre_df, post_df], axis=1)
        straight = int((both.nunique(axis=1) == 1).sum())
        all_sd_post = (post_df == "strongly disagree").all(axis=1)
        all_sd_pre = (pre_df == "strongly disagree").all(axis=1)
        add(f"- Same answer on all six pre/post items: **{straight}** ({pct(straight, n_rows)}). Often "
            "legitimate (e.g. `Strongly agree` before and after), but this is a straight-lining signal.")
        add(f"- `Strongly disagree` on all three **post** items: **{int(all_sd_post.sum())}** "
            f"({pct(int(all_sd_post.sum()), n_rows)}); {int((all_sd_post & all_sd_pre).sum())} of these also "
            "say `Strongly disagree` for all pre items.")
        rec = col("likelihood_recommend")
        if rec is not None:
            r_num = pd.to_numeric(rec, errors="coerce")
            hi = int((all_sd_post & (r_num >= 9)).sum())
            add(f"  - {hi} of these respondents still rate the training 9-10 for recommendation. That "
                "contradiction suggests some respondents may have read the scale in reverse (e.g. thinking the "
                "left-most option was best). **Review needed; do not recode automatically.**")
        order = {norm_text(l): i for i, l in enumerate(AGREE_5)}
        pre_n = pre_df.apply(lambda c: c.map(order))
        post_n = post_df.apply(lambda c: c.map(order))
        dec = (post_n.values < pre_n.values).sum(axis=1)
        drop_rows = int((dec > 0).sum())
        add(f"- Rows where at least one post item is lower than the recalled pre item: **{drop_rows}** "
            f"({pct(drop_rows, n_rows)}). Decreases can be real, but are worth checking alongside the "
            "reversed-scale signal.")
    if st is not None and ct is not None:
        add(f"- Very fast completions (under 30 s) are flagged above (2.4). The survey has 13 closed-ended items, "
            "so very short durations may mean low-effort responses. Flag them; do not drop.")
    add("")

    # 2.6 Survey versions
    add("### 2.6 Evidence of survey versions\n")
    add("Optional items are missing in **blocks by session**, not at random. That pattern suggests different "
        "form versions were used. The table shows each training date with the share of rows that have each "
        "block answered:\n")
    if td is not None:
        sig_cols = {k: col(k) for k in ["sat_presenter_engaged", "likelihood_call_988", "likelihood_recommend"]
                    if col(k) is not None}
        g = pd.DataFrame({"training_date": td.fillna("(missing)"), "county": county.map(county_key)
                          if county is not None else ""})
        for k, s in sig_cols.items():
            g[k] = s.notna()
        sat = col("sat_presenter_engaged")
        if sat is not None:
            g["off_scale_sat"] = sat.isin(["Very agree", "Satisfied"])
        if lang is not None:
            g["spanish"] = lang.map(norm_text).str.startswith("espa")
        agg = g.groupby("training_date").agg(
            n=("county", "size"),
            county=("county", lambda x: x.mode().iloc[0] if len(x.mode()) else ""),
            **{f"{k}_answered_%": (k, lambda x: round(100 * x.mean())) for k in sig_cols},
            **({"off_scale_sat_labels": ("off_scale_sat", "sum")} if sat is not None else {}),
            **({"spanish_rows": ("spanish", "sum")} if lang is not None else {}),
        ).reset_index()
        add(md_table(agg))
        add("")
        add("Observations:")
        call = col("likelihood_call_988")
        if call is not None and ct_dt is not None:
            answered = ct_dt[call.notna()]
            add(f"- `likelihood_call_988` is answered by only {int(call.notna().sum())} of {n_rows} rows "
                f"({pct(int(call.notna().sum()), n_rows)}). No response before {answered.min():%Y-%m-%d} has it, "
                "so the question appears to have been **added in spring 2026**. Even after that it appears only "
                "in some sessions, which suggests more than one form was in use at the same time.")
        if sat is not None and td is not None:
            whole = agg[(agg.filter(like="sat_presenter_engaged").iloc[:, 0] == 0) & (agg["n"] >= 5)]
            if len(whole):
                add(f"- The training-experience block is **entirely missing** for sessions on "
                    f"{', '.join(whole['training_date'])} ({int(whole['n'].sum())} rows). Those sessions do have "
                    "`likelihood_call_988`, which points to a version without the experience block.")
            add("- Elsewhere, missing experience answers are scattered (a few per session), which looks like "
                "ordinary item non-response.")
        add("- `Very agree` / `Satisfied` labels cluster in a handful of sessions, mostly April 2026 "
            "(see `off_scale_sat_labels`). They are not limited to Spanish responses, so an alternate form with "
            "different answer options is the likelier source.")
        add("- **Analyses must therefore use per-item denominators.** Missing optional items must not be read as "
            "negative answers, and trends in partially collected items must be shown with their response counts.\n")

    # 2.7 Free text
    add("### 2.7 Written feedback (aggregate only)\n")
    for name in TEXT_FIELDS:
        s = col(name)
        if s is None:
            continue
        t = s.dropna().astype(str)
        non_ans = int(t.map(norm_text).isin(NON_ANSWERS).sum())
        add(f"- **{name}**: {len(t)} responses ({pct(len(t), n_rows)}), median length {t.str.len().median():.0f} "
            f"characters, {non_ans} non-answers such as 'none' or 'n/a', {int(t.duplicated().sum())} exact "
            "repeats of another response.")
        for label, pat in PII_PATTERNS.items():
            hits = int(t.str.contains(pat, regex=True).sum())
            if hits:
                add(f"  - {hits} response(s) contain a possible **{label}**. Redact before display.")
    add("- Free text may name presenters, schools or people. The dashboard should show comments only after "
        "PII screening, and never next to small-group filters that could identify a respondent.\n")

    # 3. Question mapping
    add("## 3. Survey question mapping to business questions\n")
    qrows = []
    for f in SCHEMA:
        qrows.append({
            "Canonical field": f.name,
            "Present": "yes" if f.name in col_of else "no",
            "Category": f.category,
            "Supports": f.business_questions,
            "Meaning": f.description,
        })
    add(md_table(pd.DataFrame(qrows)))
    add("\n**Retrospective pre/post pairs** (the 'before' items are recalled after training, not measured "
        "beforehand):\n")
    for p, q in PRE_POST_PAIRS:
        add(f"- `{p}` <-> `{q}`")
    add("\nAnalytic use:")
    add("- **SQ1 (effectiveness & monitoring):** paired pre/post change per item, % at Agree or above, "
        "recommendation score (0-10, with NPS as a secondary view), likelihood to call 988, all tracked by "
        "training date and session.")
    add("- **SQ2 (patterns & relationships):** links between training-experience ratings and confidence gain or "
        "recommendation; text themes from the two open-ended questions.")
    add("- **SQ3 (communities & populations):** compare the above by county, participant role and language, "
        "with minimum group sizes. Small groups (e.g. Spanish-language responses, "
        "non-Maryland counties) are too small for reliable comparisons.\n")

    # 4. Limitations
    add("## 4. Assumptions and limitations\n")
    add("- Column meanings come from the header text only; no codebook was provided.")
    add("- The pre-training items are retrospective self-reports, so they are open to recall bias and social "
        "desirability. They measure perceived change, not tested knowledge.")
    add("- Student responses dominate. County and role are confounded: most Anne Arundel responses are students "
        "in school sessions. Community differences may reflect audience type rather than location.")
    add("- 'Session' is inferred from training date plus county. No session ID exists.")
    add("- Checks for reversed scales, very fast completions and survey versions produce flags only. They do not "
        "prove a response is invalid.\n")

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "data_profile.md").write_text("\n".join(r), encoding="utf-8")

    # Data dictionary ------------------------------------------------------------
    dd_rows = []
    for i, c in enumerate(raw.columns):
        s = raw[c]
        m = mapping[c]
        f = SCHEMA_BY_NAME.get(m["field"]) if m["field"] else None
        if f and f.dtype == "free_text":
            values = f"free text; {s.nunique()} distinct; values not listed (privacy)"
        elif f and f.name in ("response_id", "start_time", "completion_time"):
            values = f"{s.nunique()} distinct; min {s.min()}; max {s.max()}"
        elif s.nunique() <= 25:
            values = "; ".join(f"{v!r}={n}" for v, n in s.value_counts().items())
        else:
            values = f"{s.nunique()} distinct; e.g. " + "; ".join(repr(v) for v in s.value_counts().index[:5])
        dd_rows.append({
            "column_index": i,
            "original_header": c,
            "normalized_header": norm_text(c),
            "canonical_field": m["field"] or "",
            "match_method": m["method"],
            "match_confidence": m["confidence"],
            "category": f.category if f else "",
            "meaning": f.description if f else "",
            "raw_inferred_type": pd.api.types.infer_dtype(s, skipna=True),
            "canonical_type": f.dtype if f else "",
            "required": (f.required if f else ""),
            "non_missing": int(s.notna().sum()),
            "missing": int(s.isna().sum()),
            "missing_pct": round(100 * s.isna().mean(), 1),
            "n_unique": int(s.nunique()),
            "observed_values": values,
            "business_questions": f.business_questions if f else "",
        })
    pd.DataFrame(dd_rows).to_csv(outdir / "data_dictionary.csv", index=False, encoding="utf-8-sig")

    checksum_after = sha256(input_path)
    if checksum_after != checksum_before:
        sys.exit("ERROR: input file checksum changed during profiling.")

    print(f"Sheet: {sheet} | rows={n_rows} cols={n_cols}")
    print(f"Mapped {len(col_of)}/{len(SCHEMA)} canonical fields; missing required: {missing_required or 'none'}")
    print(f"Wrote {outdir / 'data_profile.md'}")
    print(f"Wrote {outdir / 'data_dictionary.csv'}")
    print("Input file unchanged (SHA-256 verified).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Excel (.xlsx) or CSV survey export")
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")
    profile(args.input, args.outdir)


if __name__ == "__main__":
    main()
