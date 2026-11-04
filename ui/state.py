"""Per-session dataset lifecycle. Works on any MutableMapping (st.session_state or a dict in tests).

Rules:
* Uploaded bytes and derived data live only in this session's state (never on disk, never in a
  global cache shared across users).
* A new file (different content hash) clears everything derived from the previous one,
  including analysis results, period caches and AI recommendations.
* A failed processing run clears previous results so nothing misleading is shown.
* `dataset_id` identifies (file content, sheet, confirmed mappings); AI results are keyed by it.
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import MutableMapping

import pandas as pd

from src.analysis_utils import AnalysisConfig
from src.analyze_q1 import run_q1
from src.clean_data import CleaningConfig, clean_survey, load_survey_file, make_public_dataset
from src.run_analysis import run_all
from src.schema_mapper import map_columns
from ui.transforms import period_subset

DERIVED = ("cleaned", "report", "results", "public_df", "dataset_id", "error", "period_cache", "ai_results",
           "processed_at")


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def clear_derived(state: MutableMapping) -> None:
    for k in DERIVED:
        state.pop(k, None)


def reset(state: MutableMapping) -> None:
    for k in list(state.keys()):
        if k.startswith(("upload_", "map_choice_")) or k in DERIVED or k in ("overrides", "ai_calls"):
            state.pop(k, None)


def register_upload(state: MutableMapping, data: bytes, filename: str) -> bool:
    """Store a new upload. Returns True if it differs from the current one (and resets derived state)."""
    h = file_hash(data)
    if state.get("upload_hash") == h:
        return False
    reset(state)
    state["upload_hash"], state["upload_name"], state["upload_bytes"] = h, filename, data
    try:
        raw, sheet, sheets = load_survey_file(io.BytesIO(data), filename=filename)
    except Exception as exc:  # noqa: BLE001
        state["upload_error"] = f"Could not read the file: {type(exc).__name__}. Upload an .xlsx or .csv export."
        return True
    state["upload_raw"], state["upload_sheet"], state["upload_sheets"] = raw, sheet, sheets
    state["overrides"] = {}
    return True


def select_sheet(state: MutableMapping, sheet: str) -> None:
    if sheet == state.get("upload_sheet"):
        return
    raw, sheet, _ = load_survey_file(io.BytesIO(state["upload_bytes"]), filename=state["upload_name"], sheet=sheet)
    state["upload_raw"], state["upload_sheet"], state["overrides"] = raw, sheet, {}
    clear_derived(state)


def mapping_preview(state: MutableMapping) -> list[dict]:
    raw = state.get("upload_raw")
    if raw is None:
        return []
    return map_columns(list(raw.columns), overrides=state.get("overrides") or {}).to_records()


def dataset_id(state: MutableMapping) -> str:
    blob = json.dumps({"file": state.get("upload_hash"), "sheet": state.get("upload_sheet"),
                       "overrides": state.get("overrides") or {}}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def process(state: MutableMapping, analysis_cfg: AnalysisConfig | None = None) -> bool:
    """Clean + analyze the current upload with confirmed overrides. Returns success."""
    clear_derived(state)
    raw = state.get("upload_raw")
    if raw is None:
        state["error"] = "No dataset uploaded."
        return False
    try:
        cfg = CleaningConfig(column_overrides=dict(state.get("overrides") or {}))
        cleaned, report = clean_survey(raw, cfg)
        if report["status"] == "blocked":
            state["report"] = report
            state["error"] = report.get("status_reason") or "No analyzable survey questions were recognized."
            return False
        results = run_all(cleaned, report, analysis_cfg)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message, never stale results
        clear_derived(state)
        state["error"] = f"Processing failed ({type(exc).__name__}). Check the column mapping and file format."
        return False
    state.update({"cleaned": cleaned, "report": report, "results": results,
                  "public_df": make_public_dataset(cleaned), "dataset_id": dataset_id(state),
                  "period_cache": {}, "ai_results": {}})
    return True


def is_ready(state: MutableMapping) -> bool:
    return state.get("results") is not None and state.get("dataset_id") == dataset_id(state)


def q1_for_period(state: MutableMapping, months: tuple[str, ...] | None) -> dict:
    """Q1 results restricted to training months (None = all responses). Cached per dataset+period."""
    if months is None:
        return state["results"]["q1"]
    cache = state.setdefault("period_cache", {})
    key = (state["dataset_id"], months)
    if key not in cache:
        cache[key] = run_q1(period_subset(state["cleaned"], months), AnalysisConfig())
    return cache[key]


def ai_result_for_current(state: MutableMapping, fingerprint: str) -> dict | None:
    r = (state.get("ai_results") or {}).get(fingerprint)
    return r if r and r.get("dataset_id") == state.get("dataset_id") else None


def store_ai_result(state: MutableMapping, result: dict) -> None:
    if result.get("dataset_id") != state.get("dataset_id"):
        return  # dataset changed while the request was running: discard
    state.setdefault("ai_results", {})[result["fingerprint"]] = result


def latest_ai_result(state: MutableMapping) -> dict | None:
    res = [r for r in (state.get("ai_results") or {}).values()
           if r.get("dataset_id") == state.get("dataset_id") and r.get("status") in ("ok", "partial")]
    return res[-1] if res else None


def public_csv(state: MutableMapping) -> bytes:
    df: pd.DataFrame = state["public_df"]
    return df.to_csv(index=False).encode("utf-8")
