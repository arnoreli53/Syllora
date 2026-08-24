from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI as OpenAIClient  # type: ignore

try:
    from openai import OpenAI as OpenAIClient  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenAIClient = None


DEFAULT_SYLLABUS_AI_MODEL = "gpt-5.4-mini"
SYLLABUS_OPENAI_TIMEOUT_SECONDS = 60.0
WEIGHT_UNIT_VALUES = {"percent", "marks", "points", "count", "unknown"}
DUE_STATUS_VALUES = {"exact", "inferred", "unknown"}
RECURRENCE_CONFIDENCE_VALUES = {"low", "moderate", "high"}
WEIGHT_SOURCE_VALUES = {"explicit", "equal_split_inferred", "conditional", "missing"}
WEIGHT_SCHEME_VALUES = {"explicit", "equal_split", "conditional", "unresolved"}


@dataclass
class SyllabusGradingContext:
    grading_unit_type: str = "unknown"
    course_total_value: str = ""
    course_total_unit: str = "unknown"
    course_start_date: str = ""
    course_end_date: str = ""
    notes: str = ""


@dataclass
class SyllabusCategory:
    name: str = ""
    total_weight_value: str = ""
    total_weight_unit: str = "unknown"
    normalized_weight_percent: str = ""
    child_count: int = 0
    counted_child_count: int = 0
    per_item_weight_value: str = ""
    per_item_weight_unit: str = "unknown"
    weight_scheme: str = "unresolved"
    weight_evidence: str = ""
    policy_notes: str = ""
    recurrence_start_date: str = ""
    recurrence_due_time: str = ""
    recurrence_interval_days: int = 0
    recurrence_confidence: str = "low"
    confidence: float = 1.0
    source_excerpt: str = ""


@dataclass
class SyllabusRow:
    item: str = ""
    component: str = ""
    due_date: str = ""
    due_time: str = ""
    weight_percent: str = ""
    raw_weight_value: str = ""
    raw_weight_unit: str = "unknown"
    weight_source: str = "missing"
    due_status: str = "unknown"
    parent_category: str = ""
    parent_total_weight_value: str = ""
    parent_total_weight_unit: str = "unknown"
    child_index: int = 0
    child_count: int = 0
    policy_notes: str = ""
    ungraded: bool = False
    status: str = "not started"
    notes: str = ""
    confidence: float = 1.0
    source_excerpt: str = ""


@dataclass
class SyllabusExtractionResult:
    mode: str = "local"
    provider: str = "local"
    course_suggestion: str = ""
    grading_context: SyllabusGradingContext = field(default_factory=SyllabusGradingContext)
    categories: list[SyllabusCategory] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: list[SyllabusRow] = field(default_factory=list)


class SyllabusExtractionError(RuntimeError):
    pass


def _append_warning_once(warnings: list[str], message: str) -> None:
    text = (message or "").strip()
    if not text or text in warnings:
        return
    warnings.append(text)


def openai_sdk_available() -> bool:
    return OpenAIClient is not None


def normalize_weight_unit(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    if raw in {"%", "percent", "percentage", "percentages"}:
        return "percent"
    if raw in {"mark", "marks"}:
        return "marks"
    if raw in {"point", "points", "pt", "pts"}:
        return "points"
    if raw in {"count", "counts", "items", "occurrences"}:
        return "count"
    if raw in WEIGHT_UNIT_VALUES:
        return raw
    return "unknown"


def normalize_due_status(value: object, *, has_due: bool = False) -> str:
    raw = str(value or "").strip().lower()
    if raw in DUE_STATUS_VALUES:
        return raw
    return "exact" if has_due else "unknown"


def normalize_recurrence_confidence(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in RECURRENCE_CONFIDENCE_VALUES:
        return raw
    return "low"


def normalize_weight_source(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in WEIGHT_SOURCE_VALUES:
        return raw
    return "missing"


def normalize_weight_scheme(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in WEIGHT_SCHEME_VALUES:
        return raw
    return "unresolved"


def normalize_status(value: str, default: str = "not started") -> str:
    allowed = {"not started", "in progress", "submitted", "graded"}
    status = (value or "").strip().lower()
    if status in allowed:
        return status
    return default if default in allowed else "not started"


def normalize_due_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    cleaned = re.sub(r"(st|nd|rd|th)", "", raw, flags=re.IGNORECASE)
    formats = (
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue

    if re.search(r"\d{4}", cleaned) is None:
        current_year = datetime.now().year
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(f"{cleaned}, {current_year}", fmt).strftime("%Y-%m-%d")
            except Exception:
                continue

    return raw


def normalize_due_time(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    lowered = raw.lower().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(lowered, fmt).strftime("%H:%M")
        except Exception:
            continue
    return raw


def combine_due_parts(due_date: str, due_time: str) -> str:
    date_part = normalize_due_date(due_date)
    time_part = normalize_due_time(due_time)
    if not date_part:
        return ""
    if not time_part:
        return date_part
    return f"{date_part} {time_part}"


def normalize_number_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = raw.replace("%", "")
    try:
        num = float(cleaned)
    except Exception:
        return ""
    return f"{num:.4f}".rstrip("0").rstrip(".")


def normalize_percent_text(value: object) -> str:
    return normalize_number_text(value)


def coerce_weight_percent(value: str) -> str:
    return normalize_percent_text(value)


def coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on", "checked"}


def _coerce_positive_int(value: object) -> int:
    try:
        number = int(float(str(value or "").strip()))
    except Exception:
        return 0
    return max(0, number)


def format_raw_weight(raw_value: str, raw_unit: str) -> str:
    value = normalize_number_text(raw_value)
    unit = normalize_weight_unit(raw_unit)
    if not value:
        return ""
    if unit == "percent":
        return f"{value}%"
    if unit in {"marks", "points"}:
        return f"{value} {unit}"
    return value


def parse_raw_weight_display(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "", "unknown"
    match = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*([A-Za-z%]+)?\s*$", raw)
    if not match:
        return normalize_number_text(raw), "unknown"
    value = normalize_number_text(match.group(1))
    unit = match.group(2) or ""
    if unit == "%":
        return value, "percent"
    normalized_unit = normalize_weight_unit(unit)
    return value, normalized_unit


def _as_float(value: object) -> float | None:
    raw = normalize_number_text(value)
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _category_key(name: str) -> str:
    return str(name or "").strip().lower()


def _normalize_weight_to_percent(value: str, unit: str, context: SyllabusGradingContext) -> str:
    amount = _as_float(value)
    if amount is None:
        return ""

    normalized_unit = normalize_weight_unit(unit)
    if normalized_unit == "percent":
        return normalize_percent_text(amount)

    if normalized_unit not in {"marks", "points"}:
        return ""

    context_unit = normalize_weight_unit(context.course_total_unit or context.grading_unit_type)
    total_amount = _as_float(context.course_total_value)
    if total_amount is None or total_amount <= 0:
        return ""
    if context_unit not in {"marks", "points"}:
        return ""
    return normalize_percent_text((amount / total_amount) * 100.0)


_RUBRIC_COMPONENT_PATTERNS = (
    "project requirement",
    "assignment requirement",
    "marking criterion",
    "marking criteria",
    "rubric",
    "grading criterion",
    "grading criteria",
    "formatting requirement",
    "submission requirement",
)

_RUBRIC_ITEM_PATTERNS = (
    "academic references",
    "references",
    "citation style",
    "apa",
    "vancouver-style",
    "vancouver style",
    "syntax",
    "spelling",
    "grammar",
    "content",
    "organization",
    "organisation",
    "creativity",
    "formatting",
    "presentation quality",
    "writing quality",
)


def looks_like_rubric_row(row: SyllabusRow) -> bool:
    item = (row.item or "").strip().lower()
    component = (row.component or "").strip().lower()
    notes = (row.notes or "").strip().lower()

    if any(token in component for token in _RUBRIC_COMPONENT_PATTERNS):
        return True

    if "marking criterion" in notes or "marking criteria" in notes or "rubric" in notes:
        return True

    if not item:
        return False

    if any(token in item for token in _RUBRIC_ITEM_PATTERNS):
        if row.due_date or row.weight_percent:
            return False
        return True

    if item.endswith(" requirement") and not row.due_date and not row.weight_percent:
        return True

    return False


def filter_deliverable_rows(rows: list[SyllabusRow]) -> tuple[list[SyllabusRow], int]:
    kept: list[SyllabusRow] = []
    removed = 0
    for row in rows:
        if looks_like_rubric_row(row):
            removed += 1
            continue
        kept.append(row)
    return kept, removed


def graded_weight_total(rows: list[SyllabusRow]) -> float:
    total = 0.0
    for row in rows:
        if row.ungraded:
            continue
        weight = normalize_percent_text(row.weight_percent)
        if not weight:
            continue
        try:
            total += float(weight)
        except Exception:
            continue
    return total


def category_weight_total(categories: list[SyllabusCategory]) -> float:
    total = 0.0
    for category in categories:
        weight = normalize_percent_text(category.normalized_weight_percent)
        if not weight:
            continue
        try:
            total += float(weight)
        except Exception:
            continue
    return total


def _row_sort_key(row: SyllabusRow) -> tuple[str, str, str]:
    return (row.due_date or "9999-99-99", row.item.lower(), row.component.lower())


def _sanitize_context(payload: object) -> SyllabusGradingContext:
    raw = payload if isinstance(payload, dict) else {}
    return SyllabusGradingContext(
        grading_unit_type=normalize_weight_unit(raw.get("grading_unit_type", "")),
        course_total_value=normalize_number_text(raw.get("course_total_value", "")),
        course_total_unit=normalize_weight_unit(raw.get("course_total_unit", raw.get("grading_unit_type", ""))),
        course_start_date=normalize_due_date(str(raw.get("course_start_date", "") or "")),
        course_end_date=normalize_due_date(str(raw.get("course_end_date", "") or "")),
        notes=str(raw.get("notes", "") or "").strip()[:500],
    )


def _sanitize_category(category: object) -> SyllabusCategory:
    raw = category if isinstance(category, dict) else {}
    confidence_raw = raw.get("confidence", 1.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except Exception:
        confidence = 1.0

    return SyllabusCategory(
        name=str(raw.get("name", "") or "").strip()[:120],
        total_weight_value=normalize_number_text(raw.get("total_weight_value", "")),
        total_weight_unit=normalize_weight_unit(raw.get("total_weight_unit", "")),
        normalized_weight_percent=normalize_percent_text(raw.get("normalized_weight_percent", "")),
        child_count=_coerce_positive_int(raw.get("child_count", 0)),
        counted_child_count=_coerce_positive_int(raw.get("counted_child_count", 0)),
        per_item_weight_value=normalize_number_text(raw.get("per_item_weight_value", "")),
        per_item_weight_unit=normalize_weight_unit(raw.get("per_item_weight_unit", "")),
        weight_scheme=normalize_weight_scheme(raw.get("weight_scheme", "")),
        weight_evidence=str(raw.get("weight_evidence", "") or "").strip()[:500],
        policy_notes=str(raw.get("policy_notes", "") or "").strip()[:500],
        recurrence_start_date=normalize_due_date(str(raw.get("recurrence_start_date", "") or "")),
        recurrence_due_time=normalize_due_time(str(raw.get("recurrence_due_time", "") or "")),
        recurrence_interval_days=_coerce_positive_int(raw.get("recurrence_interval_days", 0)),
        recurrence_confidence=normalize_recurrence_confidence(raw.get("recurrence_confidence", "")),
        confidence=confidence,
        source_excerpt=str(raw.get("source_excerpt", "") or "").strip()[:500],
    )


def _sanitize_row(row: dict, default_status: str = "not started") -> SyllabusRow:
    confidence_raw = row.get("confidence", 1.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except Exception:
        confidence = 1.0

    item = str(row.get("item", "") or "").strip()
    component = str(row.get("component", "") or "").strip()
    due_date = normalize_due_date(str(row.get("due_date", "") or ""))
    due_time = normalize_due_time(str(row.get("due_time", "") or ""))
    weight = normalize_percent_text(str(row.get("weight_percent", "") or row.get("normalized_weight_percent", "") or ""))
    raw_weight_value = normalize_number_text(row.get("raw_weight_value", ""))
    raw_weight_unit = normalize_weight_unit(row.get("raw_weight_unit", ""))
    weight_source = normalize_weight_source(row.get("weight_source", ""))
    ungraded = coerce_bool(row.get("ungraded", False))
    # Syllabus extraction should never infer real progress state. Treat every
    # extracted task as a fresh, not-started item locally regardless of model output.
    status = normalize_status(default_status, default_status)
    notes = str(row.get("notes", "") or "").strip()
    source_excerpt = str(row.get("source_excerpt", "") or "").strip()
    due_status = normalize_due_status(row.get("due_status", ""), has_due=bool(due_date))
    parent_category = str(row.get("parent_category", "") or "").strip()
    parent_total_weight_value = normalize_number_text(row.get("parent_total_weight_value", ""))
    parent_total_weight_unit = normalize_weight_unit(row.get("parent_total_weight_unit", ""))
    child_index = _coerce_positive_int(row.get("child_index", 0))
    child_count = _coerce_positive_int(row.get("child_count", 0))
    policy_notes = str(row.get("policy_notes", "") or "").strip()

    if ungraded:
        weight = ""

    if not item:
        item = component or parent_category or "Assessment"
    if not component and parent_category:
        component = parent_category
    if due_date and due_status == "unknown":
        due_status = "exact"
    if not due_date and due_status == "exact":
        due_status = "unknown"

    return SyllabusRow(
        item=item[:200],
        component=component[:80],
        due_date=due_date,
        due_time=due_time,
        weight_percent=weight,
        raw_weight_value=raw_weight_value,
        raw_weight_unit=raw_weight_unit,
        weight_source=weight_source,
        due_status=due_status,
        parent_category=parent_category[:120],
        parent_total_weight_value=parent_total_weight_value,
        parent_total_weight_unit=parent_total_weight_unit,
        child_index=child_index,
        child_count=child_count,
        policy_notes=policy_notes[:500],
        ungraded=ungraded,
        status=status,
        notes=notes[:500],
        confidence=confidence,
        source_excerpt=source_excerpt[:500],
    )


def _build_category_lookup(categories: list[SyllabusCategory]) -> dict[str, SyllabusCategory]:
    return {_category_key(category.name): category for category in categories if category.name.strip()}


_EQUAL_WEIGHT_HINTS = (
    "equal weight",
    "equally weighted",
    "equally-weighted",
    "equal weighting",
)

_CONDITIONAL_WEIGHT_HINTS = (
    "best ",
    "lowest ",
    "highest ",
    "middle ",
    "replace",
    "replacement",
    "dropped",
    "drop ",
    "mulligan",
)


def _category_counted_child_count(category: SyllabusCategory) -> int:
    counted = category.counted_child_count or category.child_count
    return max(0, counted)


def _resolve_category_weight_scheme(category: SyllabusCategory) -> str:
    if category.per_item_weight_value:
        return "explicit"
    if category.weight_scheme != "unresolved":
        return category.weight_scheme
    evidence = " ".join(
        part.strip().lower()
        for part in (category.weight_evidence, category.policy_notes, category.source_excerpt)
        if part and part.strip()
    )
    if any(token in evidence for token in _EQUAL_WEIGHT_HINTS):
        return "equal_split"
    if any(token in evidence for token in _CONDITIONAL_WEIGHT_HINTS):
        return "conditional"
    return "unresolved"


def _infer_child_index(row: SyllabusRow) -> int:
    if row.child_index > 0:
        return row.child_index
    match = re.search(r"(\d+)\s*$", row.item)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return 0
    return 0


def _singularize_label(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "Task"
    lowered = raw.lower()
    mapping = {
        "quizzes": "Quiz",
        "labs": "Lab",
        "discussions": "Discussion",
        "assignments": "Assignment",
        "modules": "Module",
    }
    if lowered in mapping:
        return mapping[lowered]
    if raw.endswith("ies"):
        return raw[:-3] + "y"
    if raw.endswith("s") and len(raw) > 1:
        return raw[:-1]
    return raw


def _series_name_prefix(category: SyllabusCategory, rows: list[SyllabusRow]) -> str:
    prefixes: list[str] = []
    for row in rows:
        match = re.match(r"^(.*?)(\d+)\s*$", row.item.strip())
        if not match:
            continue
        prefixes.append(match.group(1).strip())
    if prefixes and len(set(prefixes)) == 1 and prefixes[0]:
        return prefixes[0]
    return _singularize_label(category.name)


def _merge_notes(*parts: str) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return "; ".join(ordered)


def _policy_drop_count(text: str) -> int:
    raw = str(text or "").strip().lower()
    if not raw:
        return 0
    if "drop" not in raw and "mulligan" not in raw:
        return 0
    digit_match = re.search(r"(\d+)\s+(?:lowest\s+)?(?:quiz|lab|item|items)?\s*(?:is\s+)?(?:are\s+)?(?:dropped|drop|mulligan)", raw)
    if digit_match:
        try:
            return int(digit_match.group(1))
        except Exception:
            return 0
    if any(token in raw for token in ("lowest 1", "one drop", "one quiz dropped", "lowest dropped", "one mulligan")):
        return 1
    return 0


def _date_in_bounds(date_text: str, context: SyllabusGradingContext) -> bool:
    if not date_text:
        return True
    try:
        target = datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        return True
    if context.course_start_date:
        try:
            start = datetime.strptime(context.course_start_date, "%Y-%m-%d").date()
            if target < start:
                return False
        except Exception:
            pass
    if context.course_end_date:
        try:
            end = datetime.strptime(context.course_end_date, "%Y-%m-%d").date()
            if target > end:
                return False
        except Exception:
            pass
    return True


def _apply_weight_normalization(
    rows: list[SyllabusRow],
    categories: list[SyllabusCategory],
    context: SyllabusGradingContext,
) -> None:
    categories_by_key = _build_category_lookup(categories)
    rows_by_category: dict[str, list[SyllabusRow]] = {}
    for row in rows:
        key = _category_key(row.parent_category or row.component)
        if key:
            rows_by_category.setdefault(key, []).append(row)

    for category in categories:
        if not category.normalized_weight_percent and category.total_weight_value:
            category.normalized_weight_percent = _normalize_weight_to_percent(
                category.total_weight_value,
                category.total_weight_unit,
                context,
            )
        category.weight_scheme = _resolve_category_weight_scheme(category)
        if category.counted_child_count <= 0:
            category.counted_child_count = category.child_count

    for row in rows:
        row_has_explicit_weight = row.weight_source == "explicit" and bool(normalize_percent_text(row.weight_percent))
        row_has_explicit_raw = bool(row.raw_weight_value)
        category = categories_by_key.get(_category_key(row.parent_category or row.component))
        if category is not None:
            row.parent_category = category.name
            row.component = row.component or category.name
            row.parent_total_weight_value = row.parent_total_weight_value or category.total_weight_value
            row.parent_total_weight_unit = normalize_weight_unit(row.parent_total_weight_unit or category.total_weight_unit)
            row.child_count = row.child_count or category.child_count
            row.policy_notes = row.policy_notes or category.policy_notes
            if not row.raw_weight_value and category.per_item_weight_value:
                row.raw_weight_value = category.per_item_weight_value
                row.raw_weight_unit = normalize_weight_unit(category.per_item_weight_unit)

        if row.ungraded:
            row.weight_percent = ""
            row.weight_source = "missing"
            continue

        normalized = ""
        source = "missing"

        if row_has_explicit_raw and row.raw_weight_unit in {"marks", "points", "percent"}:
            normalized = _normalize_weight_to_percent(row.raw_weight_value, row.raw_weight_unit, context)
            source = "explicit"
        elif row_has_explicit_weight:
            normalized = normalize_percent_text(row.weight_percent)
            source = "explicit"
        elif category is not None and category.per_item_weight_value and category.per_item_weight_unit in {"marks", "points", "percent"}:
            normalized = _normalize_weight_to_percent(category.per_item_weight_value, category.per_item_weight_unit, context)
            source = "explicit"
        elif category is not None and category.weight_scheme == "equal_split":
            counted_child_count = _category_counted_child_count(category)
            parent_percent = normalize_percent_text(category.normalized_weight_percent)
            linked_rows = rows_by_category.get(_category_key(category.name), [])
            graded_linked_rows = [child for child in linked_rows if not child.ungraded]
            if counted_child_count > 0 and parent_percent and len(graded_linked_rows) <= counted_child_count:
                try:
                    normalized = normalize_percent_text(float(parent_percent) / counted_child_count)
                except Exception:
                    normalized = ""
                source = "equal_split_inferred" if normalized else "missing"
            else:
                source = "conditional" if counted_child_count > 0 and len(graded_linked_rows) > counted_child_count else "missing"
        elif category is not None and category.weight_scheme == "conditional":
            source = "conditional"

        row.weight_percent = normalize_percent_text(normalized) if normalized else ""
        row.weight_source = source


def _apply_recurrence_generation(
    rows: list[SyllabusRow],
    categories: list[SyllabusCategory],
    context: SyllabusGradingContext,
    warnings: list[str],
) -> list[SyllabusRow]:
    categories_by_key = _build_category_lookup(categories)
    grouped: dict[str, list[SyllabusRow]] = {}
    passthrough: list[SyllabusRow] = []

    for row in rows:
        key = _category_key(row.parent_category or row.component)
        if key and key in categories_by_key:
            grouped.setdefault(key, []).append(row)
        else:
            passthrough.append(row)

    out: list[SyllabusRow] = list(passthrough)
    for key, category in categories_by_key.items():
        existing = grouped.get(key, [])
        if category.child_count <= 0:
            out.extend(existing)
            continue

        indexed: dict[int, SyllabusRow] = {}
        for row in existing:
            index = _infer_child_index(row)
            if index > 0:
                row.child_index = index
                indexed[index] = row

        name_prefix = _series_name_prefix(category, existing)
        can_infer_dates = (
            category.recurrence_interval_days > 0
            and bool(category.recurrence_start_date)
            and category.recurrence_confidence in {"high", "moderate"}
        )

        for index in range(1, category.child_count + 1):
            row = indexed.get(index)
            if row is None:
                row = SyllabusRow(
                    item=f"{name_prefix} {index}",
                    component=category.name,
                    parent_category=category.name,
                    child_index=index,
                    child_count=category.child_count,
                    raw_weight_value=category.per_item_weight_value,
                    raw_weight_unit=category.per_item_weight_unit,
                    parent_total_weight_value=category.total_weight_value,
                    parent_total_weight_unit=category.total_weight_unit,
                    policy_notes=category.policy_notes,
                    status="not started",
                    due_status="unknown",
                    notes="Generated from repeated task count in syllabus.",
                    confidence=min(category.confidence, 0.85),
                    source_excerpt=category.source_excerpt,
                )
            else:
                row.child_index = index
                row.child_count = row.child_count or category.child_count
                row.parent_category = row.parent_category or category.name
                row.component = row.component or category.name
                row.parent_total_weight_value = row.parent_total_weight_value or category.total_weight_value
                row.parent_total_weight_unit = row.parent_total_weight_unit or category.total_weight_unit
                row.policy_notes = row.policy_notes or category.policy_notes
                if not row.raw_weight_value and category.per_item_weight_value:
                    row.raw_weight_value = category.per_item_weight_value
                    row.raw_weight_unit = category.per_item_weight_unit

            if not row.due_date and can_infer_dates:
                try:
                    base_date = datetime.strptime(category.recurrence_start_date, "%Y-%m-%d")
                    inferred = (base_date + timedelta(days=category.recurrence_interval_days * (index - 1))).strftime("%Y-%m-%d")
                except Exception:
                    inferred = ""
                if inferred and _date_in_bounds(inferred, context):
                    row.due_date = inferred
                    row.due_time = row.due_time or category.recurrence_due_time
                    row.due_status = "exact" if index == 1 else "inferred"
                    if category.recurrence_confidence == "moderate":
                        row.notes = _merge_notes(
                            row.notes,
                            "Dates inferred provisionally from syllabus recurrence; review against the full schedule.",
                        )
                elif not row.due_date:
                    row.due_status = "unknown"

            if row.due_date and row.due_status == "unknown":
                row.due_status = "exact" if row.child_index == 1 and row.due_date == category.recurrence_start_date else row.due_status

            out.append(row)

        if can_infer_dates:
            if category.recurrence_confidence == "moderate":
                _append_warning_once(
                    warnings,
                    f"{category.name}: repeated dates were inferred provisionally from the syllabus cadence and should be reviewed.",
                )
            else:
                _append_warning_once(
                    warnings,
                    f"{category.name}: repeated dates were inferred from the stated cadence.",
                )
        elif category.child_count > len(existing):
            _append_warning_once(
                warnings,
                f"{category.name}: repeated task instances were preserved from the grading scheme, but some dates remain unknown.",
            )

    return out


def _dedupe_rows(rows: list[SyllabusRow]) -> list[SyllabusRow]:
    seen: set[tuple[str, str, str]] = set()
    out: list[SyllabusRow] = []
    for row in rows:
        if not row.item.strip():
            continue
        key = (row.item.lower(), row.component.lower(), combine_due_parts(row.due_date, row.due_time))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=_row_sort_key)
    return out


def _sample_row_names(rows: list[SyllabusRow], *, limit: int = 3) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = row.item.strip()
        component = row.component.strip()
        if component:
            label = f"{label} ({component})"
        if not label:
            continue
        if label in seen:
            continue
        seen.add(label)
        names.append(label)
        if len(names) >= limit:
            break
    return ", ".join(names)


def build_syllabus_extraction_prompt() -> str:
    return """
  Extract syllabus data using a two-layer model: first recover grading context and parent categories, then recover actionable child tasks.

Core objective:
Produce structured, import-ready task rows only for concrete student deliverables. Keep grading metadata, category totals, recurrence rules, and policy notes separate from child task rows.

General principles:
- A graded category is not automatically a task.
- A class meeting, lecture, workshop, synchronous session, lab session, office hour, module section, or schedule row is not itself a task unless the syllabus explicitly identifies a graded deliverable tied to it.
- Do not invent tasks from instructional dates alone.
- Do not merge repeated quizzes, labs, discussions, journals, projects, modules, or assignments into one umbrella task when the syllabus supports multiple child items.
- Do not invent discussion reply counts, weekly patterns, equal splits, or due dates unless the syllabus supports them.
- Do not return rubric bullets, formatting requirements, reading reminders, participation expectations, citation rules, technology requirements, or other non-deliverable checklist text as tasks.
- Keep notes short, factual, and tied to grading or due-date uncertainty only.

Step 1: Recover grading context
Recover, when available:
- course total grading basis
- course total value
- grading unit type for the course and for each category: percentage, marks/points, pass-fail, or count-only
- course timeline bounds
- exam period or final assessment window
- recurrence anchors
- due-time conventions if explicitly stated

Never multiply decimal raw marks into percentages unless normalization against the course total is explicitly possible.

Step 2: Recover parent grading categories
For each grading category, recover:
- category name
- raw total value
- raw total unit
- normalized category percent when recoverable
- counted_child_count if stated or recoverable
- weight_scheme
- weight_evidence
- recurrence anchors
- category-level policy notes

Valid weight_scheme values:
- explicit_item_weights
- explicit_per_item_category_weight
- equal_split_inferred
- conditional
- unresolved
- ungraded

Use this hierarchy for weights:
1. explicit item weight
2. explicit per-item category weight
3. equal split only when clearly justified
4. otherwise conditional or unresolved

Weight inference hierarchy:
- If an item-level weight is explicitly stated, use it exactly.
- If a category explicitly states that each child is worth a fixed amount, use that fixed amount for every concrete child row.
- If only a category total is stated, keep the parent total on the category and leave child raw weights blank unless equal split is clearly justified.
- Equal split is clearly justified only when all of the following are true:
  1. the category total is known
  2. the relevant graded child count is known
  3. there is evidence that the graded child items are equally weighted, or there is no conflicting per-item weighting and the named child set is the full graded set
  4. the concrete child rows can be identified reliably
- If weights are performance-based, best-of, lowest-dropped, replacement-based, rank-based, or otherwise non-uniform, mark the category weight_scheme as conditional and do not invent fixed child weights.
- Lowest-drop, dropped-item, bonus, replacement, or attendance-eligibility rules stay in policy notes and must not reduce the number of task instances.

Critical rule for resolved child weights:
- If equal split is justified and concrete child rows have been identified, you must write the inferred per-item value into each child row.
- Do not leave child weight fields blank while describing the inference only in notes.
- Notes must never be the only place where a resolved weight inference appears.
- For every child row with a resolved weight, always fill all of the following consistently:
  raw_weight_value,
  raw_weight_unit,
  weight_percent,
  weight_source.
- If a child row’s weight is described as resolved or inferred in notes, the structured row fields must not be blank.

Parent-child reconciliation across sections:
- Parent grading categories may appear in one section while named dated child tasks appear elsewhere in the syllabus.
- Reconcile parent categories with named child tasks across sections whenever the match is reliable.
- When a parent category total is stated and named dated child items are listed elsewhere, apply equal-split inference if the child count is determinable and no item-specific weights override it.
- Prefer named dated child items over synthetic placeholder rows.
- Do not create both real named children and extra synthetic children for the same category.

Repeated categories and child creation:
- If a repeated category states a child count, preserve that count at the category level.
- Create child tasks for the full series only when the syllabus also provides at least one of:
  1. explicit child names
  2. explicit graded deliverables
  3. a concrete recurrence schedule that clearly applies to the graded child artifacts themselves rather than merely to class meetings or instructional sessions
- A child count alone supports category metadata, not child row creation.
- Do not create placeholder child rows merely because a category is repeated or countable.
- If a category is graded but underspecified, keep it as category metadata with unresolved flags instead of forcing importable task rows.
- If a category states that repeated child assessments are averaged to determine the final category grade, classify the child rows with a dedicated weight scheme such as averaged_component rather than generic conditional.
- Do not treat averaged repeated rows as unresolved when the category total and counted child count are known.
- For averaged components, preserve the parent category total and mark each child row as part of an averaged component.
- If the UI requires a per-row planning weight, a provisional normalized weight may be computed as category_total divided by counted_child_count, but it must be marked as averaged/provisional rather than explicit.
- Do not use generic conditional when the syllabus explicitly describes the grading mechanism as an average across repeated child assessments.
- Reserve conditional for performance-based, replacement-based, best-of, lowest-drop, or other non-uniform redistribution schemes.
- A graded parent assessment may contain concrete required subtasks.
- When the syllabus describes multiple required actions that together determine one parent grade, create one weighted parent row and separate unweighted child subtasks.
- Do not assign separate weights to required subtasks unless the syllabus explicitly does so.
- Examples of required subtasks include:
    - initial discussion post,
    - peer responses,
    - draft submission,
    - reflection step,
    - worksheet completion,
    - presentation upload,
    - quiz attempt window,
    - or other concrete actions that are required to complete the graded parent task.
- If a rubric or task description shows that a parent grade depends on both an initial submission and subsequent peer interaction, preserve both:
    - the weighted parent row for the assessment
    - unweighted actionable child subtasks for the required steps
- Unweighted child subtasks should include:
    - parent_item_name,
    - subtask_type,
    - due_date,
    - due_time,
    - due_status,
    - ungraded=true,
    - weight_source=child_unweighted,
    - and short policy notes.
- For discussion-based assessments, distinguish between:
- the graded parent discussion/module engagement assessment
- the concrete child actions required to earn that grade, such as one initial post and a minimum number of substantive replies.
- If the syllabus specifies a minimum number of replies or posts, create those as unweighted child subtasks under the parent assessment.

Actionable-task rule:
Create child rows only for concrete student actions such as:
- Quiz 1
- Journal Entry 2
- Lab 3
- Midterm
- Final Project
- Discussion Post 4
- Module 5 Reflection
- Interview
- Presentation
- Portfolio Item 3

Use the simplest actionable task names available.
Prefer names explicitly used by the syllabus.

Due-date extraction rules:
Every child row must have due_status:
- exact
- inferred
- unknown

Exact due date:
- Use due_status exact only when the syllabus directly states the due date for that task.

Inferred due date:
- Use due_status inferred only when the syllabus provides enough evidence to derive the due date from a deliverable-specific recurrence pattern or clearly tied schedule structure.
- Do not infer task dates from lecture dates, class meetings, workshops, synchronous sessions, office hours, or general course schedule rows unless the deliverable is explicitly tied to those dates.
- If the syllabus provides a repeated count plus start date plus recurrence cadence for the deliverable itself, generate the full due-date series consistently.
- Carry forward the recurrence due time across the entire series when stated.
- If cadence wording is weaker but the pattern is still clear for the deliverable itself, infer provisionally and mark due_status inferred with a review note.
- Stop inferred series at the explicit child count.
- Keep inferred dates within the course timeline if the syllabus provides schedule bounds.

Unknown due date:
- Use due_status unknown when the item is clearly graded but no reliable date can be recovered.
- Finals, interviews, exams, and projects without exact dates should remain unknown if the syllabus only gives a general exam window or says the date will be announced later.

Session-date rule:
- A scheduled class session is not a task.
- Do not convert lectures, synchronous sessions, workshops, labs, tutorials, or weekly meetings into child tasks unless the syllabus explicitly identifies a graded deliverable attached to those sessions.
- Attendance requirements or eligibility conditions tied to sessions do not, by themselves, justify creating deliverable rows.

Recurring-component rule:
- Components described as weekly, recurring, or repeated should only become child task rows if the recurring graded artifacts themselves are concretely identifiable.
- If the syllabus says something like “weekly activities” or “ten in-class products” but does not identify the actual child deliverables or their exact due instances, keep the category and count metadata but do not instantiate speculative child rows.

Ungraded items:
- Only create rows for ungraded tasks when the syllabus explicitly describes them as concrete student actions and the extraction mode allows ungraded rows.
- Otherwise do not import generic reading reminders, “review syllabus,” or preparatory instructions as tasks.
- Preserve category and row ungraded status separately from weight resolution.

Output requirements:
Return:
1. grading_context
2. categories
3. rows

grating_context should include:
- course_total_value
- course_total_unit
- normalized_course_total_percent when recoverable
- schedule_start
- schedule_end
- final_assessment_window if recoverable
- grading_basis_notes

categories should include for each category:
- category_name
- raw_total_value
- raw_total_unit
- weight_percent when recoverable
- counted_child_count
- weight_scheme
- weight_evidence
- recurrence anchors
- policy_notes
- unresolved flags when applicable

rows should include for each task row:
- item_name
- parent_category
- child_index
- child_count
- due_date
- due_time
- due_status
- raw_weight_value
- raw_weight_unit
- weight_percent
- weight_source
- ungraded
- status default if recoverable
- policy_notes

Field formatting:
- Use ISO YYYY-MM-DD for due_date.
- Use 24-hour HH:MM for due_time.
- Keep raw_weight_value numeric when possible.
- Keep raw_weight_unit literal, such as percent, marks, points.
- weight_source should distinguish:
  explicit,
  explicit_per_item_category_weight,
  equal_split_inferred,
  conditional,
  missing,
  ungraded.

Consistency rules:
- If a resolved weight exists, row weight fields must be populated.
- If a row is unresolved, explain why briefly in policy_notes.
- Do not assign a parent category’s total weight to every child task.
- Do not suppress real child rows just because the parent category is also present.
- Do not duplicate the same task across categories.
- Prefer fewer rows with higher confidence over speculative row generation.

Review-first behavior:
When the syllabus provides enough information to know that a category exists but not enough to instantiate trustworthy child rows, preserve:
- category total
- child count if known
- recurrence hints
- unresolved reason
but do not create speculative child tasks.

Final check before returning:
- Every concrete dated child item should have a parent category if recoverable.
- Any equal-split inference described in notes must also appear in the row fields.
- No child rows should be created from class meetings alone.
- No recurring category should be turned into placeholder children without concrete deliverable evidence.
- Top-level category totals and child weights must remain logically consistent.
""".strip()


def build_syllabus_validation_warnings(
    rows: list[SyllabusRow],
    *,
    categories: list[SyllabusCategory] | None = None,
    grading_context: SyllabusGradingContext | None = None,
) -> list[str]:
    warnings: list[str] = []
    if not rows:
        return warnings

    categories = categories or []
    grading_context = grading_context or SyllabusGradingContext()

    if categories:
        normalized_categories = [category for category in categories if normalize_percent_text(category.normalized_weight_percent)]
        missing_category_weights = [category for category in categories if not normalize_percent_text(category.normalized_weight_percent)]
        if normalized_categories and not missing_category_weights:
            total = category_weight_total(categories)
            if total < 99.0 or total > 101.0:
                _append_warning_once(
                    warnings,
                    f"Top-level category weights total {total:.2f}%, not about 100%. Review the grading scheme normalization.",
                )
        elif normalized_categories and missing_category_weights:
            _append_warning_once(
                warnings,
                "Some top-level grading categories do not have normalized weights yet, so course-total validation was skipped.",
            )

        rows_by_parent: dict[str, list[SyllabusRow]] = {}
        for row in rows:
            parent_key = _category_key(row.parent_category or row.component)
            if parent_key:
                rows_by_parent.setdefault(parent_key, []).append(row)

        categories_by_key = _build_category_lookup(categories)
        for parent_key, child_rows in rows_by_parent.items():
            category = categories_by_key.get(parent_key)
            if category is None:
                continue
            normalized_parent = _as_float(category.normalized_weight_percent)
            conditional_children = [child for child in child_rows if child.weight_source == "conditional"]
            weighted_children = [
                child
                for child in child_rows
                if child.weight_source in {"explicit", "equal_split_inferred"}
                and normalize_percent_text(child.weight_percent)
            ]
            missing_children = [
                child
                for child in child_rows
                if not child.ungraded and child.weight_source == "missing"
            ]
            if normalized_parent is not None and conditional_children:
                _append_warning_once(
                    warnings,
                    f"{category.name}: the category total is known, but per-item weights are conditional and were left unresolved.",
                )
                continue
            if normalized_parent is None or not weighted_children:
                continue
            if missing_children:
                _append_warning_once(
                    warnings,
                    f"{category.name}: some child tasks do not have normalized weights yet, so parent-child weight validation was skipped.",
                )
                continue
            child_weights = sorted(
                (float(normalize_percent_text(child.weight_percent)) for child in weighted_children if normalize_percent_text(child.weight_percent)),
                reverse=True,
            )
            drop_count = _policy_drop_count(category.policy_notes)
            effective_weights = child_weights[drop_count:] if drop_count > 0 and len(child_weights) > drop_count else child_weights
            child_total = sum(effective_weights)
            if abs(child_total - normalized_parent) > 0.25:
                _append_warning_once(
                    warnings,
                    f"{category.name}: child-task weights total {child_total:.2f}% but the parent category is {normalized_parent:.2f}%.",
                )

    graded_rows = [row for row in rows if not row.ungraded]
    inferred_weight_rows = [row for row in graded_rows if row.weight_source == "equal_split_inferred"]
    if inferred_weight_rows:
        _append_warning_once(
            warnings,
            f"Some task weights were inferred from equal-split rules and should be reviewed: {_sample_row_names(inferred_weight_rows)}.",
        )
    conditional_weight_rows = [row for row in graded_rows if row.weight_source == "conditional"]
    if conditional_weight_rows:
        _append_warning_once(
            warnings,
            f"Some graded tasks use conditional weighting and were left without fixed per-item weights: {_sample_row_names(conditional_weight_rows)}.",
        )
    weighted_rows = [
        row
        for row in graded_rows
        if row.weight_source in {"explicit", "equal_split_inferred"} and normalize_percent_text(row.weight_percent)
    ]
    missing_weight_rows = [row for row in graded_rows if row.weight_source == "missing"]
    if weighted_rows and missing_weight_rows and not categories:
        _append_warning_once(
            warnings,
            "Some graded tasks do not have direct weights yet, so total-weight validation was skipped until those rows are normalized.",
        )

    duplicate_groups: dict[tuple[str, str, str], list[SyllabusRow]] = {}
    for row in rows:
        key = (
            row.item.strip().lower(),
            row.component.strip().lower(),
            combine_due_parts(row.due_date, row.due_time).strip(),
        )
        duplicate_groups.setdefault(key, []).append(row)
    probable_duplicates = [group for group in duplicate_groups.values() if group[0].item.strip() and len(group) > 1]
    if probable_duplicates:
        _append_warning_once(
            warnings,
            f"Possible duplicate tasks detected: {_sample_row_names([group[0] for group in probable_duplicates])}.",
        )

    inferred_rows = [row for row in rows if row.due_status == "inferred"]
    if inferred_rows:
        _append_warning_once(
            warnings,
            f"Some repeated tasks use inferred dates and should be reviewed: {_sample_row_names(inferred_rows)}.",
        )
    unknown_dates = [row for row in rows if not row.ungraded and row.due_status == "unknown"]
    if unknown_dates:
        _append_warning_once(
            warnings,
            f"Some graded tasks have genuinely unresolved due dates: {_sample_row_names(unknown_dates)}.",
        )

    uncertain_rows = [
        row
        for row in rows
        if row.confidence < 0.75
        or row.due_status == "inferred"
        or (not row.ungraded and row.weight_source != "explicit")
        or "infer" in row.notes.lower()
        or "assum" in row.notes.lower()
        or "uncertain" in row.notes.lower()
    ]
    if uncertain_rows:
        _append_warning_once(
            warnings,
            f"Some task structures appear inferred and should be checked: {_sample_row_names(uncertain_rows)}.",
        )

    return warnings


def read_text_from_file(path: str) -> str:
    path = path.strip()
    if not path:
        return ""
    if path.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if path.lower().endswith(".docx"):
        try:
            import docx  # type: ignore

            document = docx.Document(path)
            return "\n".join(p.text for p in document.paragraphs)
        except Exception:
            return ""
    if path.lower().endswith(".pdf"):
        try:
            import PyPDF2  # type: ignore

            out: list[str] = []
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    out.append(page.extract_text() or "")
            return "\n".join(out)
        except Exception:
            return ""
    return ""


def parse_syllabus_text(text: str) -> list[SyllabusRow]:
    rows: list[SyllabusRow] = []
    date_patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?\b",
    ]
    weight_pat = r"(\d{1,3})\s*%"

    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 6:
            continue

        due = ""
        for pattern in date_patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                due = match.group(1) if match.lastindex else match.group(0)
                break
        if not due:
            continue

        norm_due = normalize_due_date(due)
        weight_match = re.search(weight_pat, line)
        weight = weight_match.group(1) if weight_match else ""

        item = line
        item = re.sub(weight_pat, "", item)
        item = item.replace(due, "")
        item = re.sub(r"\s+", " ", item).strip(" -:|\t")
        if len(item) < 3:
            item = "Assessment"

        rows.append(
            SyllabusRow(
                item=item[:200],
                due_date=norm_due,
                due_status="exact",
                weight_percent=weight,
                raw_weight_value=weight,
                raw_weight_unit="percent" if weight else "unknown",
                weight_source="explicit" if weight else "missing",
                ungraded=not bool(weight),
                status="not started",
                confidence=0.85,
            )
        )

    return _dedupe_rows(rows)


class LocalSyllabusExtractor:
    provider = "local"

    def extract(self, path: str) -> SyllabusExtractionResult:
        text = read_text_from_file(path)
        if not text:
            raise SyllabusExtractionError(
                "Could not read syllabus text. Try a TXT/DOCX syllabus, or export the PDF text."
            )

        rows = parse_syllabus_text(text)
        if not rows:
            raise SyllabusExtractionError(
                "No dated items detected in syllabus. Try a different file or import CSV instead."
            )

        return SyllabusExtractionResult(
            mode="local",
            provider="local",
            grading_context=SyllabusGradingContext(),
            categories=[],
            rows=rows,
        )


class OpenAISyllabusExtractor:
    provider = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_SYLLABUS_AI_MODEL, default_status: str = "not started") -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_SYLLABUS_AI_MODEL).strip() or DEFAULT_SYLLABUS_AI_MODEL
        self.default_status = normalize_status(default_status)
        self._client = None

    def cancel(self) -> None:
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def extract(self, path: str) -> SyllabusExtractionResult:
        if OpenAIClient is None:
            raise SyllabusExtractionError("The OpenAI Python package is not installed.")
        if not self.api_key:
            raise SyllabusExtractionError("No OpenAI API key is configured.")

        try:
            client = OpenAIClient(
                api_key=self.api_key,
                timeout=SYLLABUS_OPENAI_TIMEOUT_SECONDS,
                max_retries=0,
            )
            self._client = client
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as exc:
            self._client = None
            raise SyllabusExtractionError(str(exc)) from exc

        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        encoded = base64.b64encode(raw).decode("ascii")
        prompt = build_syllabus_extraction_prompt()

        schema = {
            "type": "object",
            "properties": {
                "course_suggestion": {"type": "string"},
                "grading_context": {
                    "type": "object",
                    "properties": {
                        "grading_unit_type": {"type": "string"},
                        "course_total_value": {"type": "string"},
                        "course_total_unit": {"type": "string"},
                        "course_start_date": {"type": "string"},
                        "course_end_date": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "grading_unit_type",
                        "course_total_value",
                        "course_total_unit",
                        "course_start_date",
                        "course_end_date",
                        "notes",
                    ],
                    "additionalProperties": False,
                },
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "total_weight_value": {"type": "string"},
                            "total_weight_unit": {"type": "string"},
                            "normalized_weight_percent": {"type": "string"},
                            "child_count": {"type": "integer"},
                            "counted_child_count": {"type": "integer"},
                            "per_item_weight_value": {"type": "string"},
                            "per_item_weight_unit": {"type": "string"},
                            "weight_scheme": {"type": "string"},
                            "weight_evidence": {"type": "string"},
                            "policy_notes": {"type": "string"},
                            "recurrence_start_date": {"type": "string"},
                            "recurrence_due_time": {"type": "string"},
                            "recurrence_interval_days": {"type": "integer"},
                            "recurrence_confidence": {"type": "string"},
                            "confidence": {"type": "number"},
                            "source_excerpt": {"type": "string"},
                        },
                        "required": [
                            "name",
                            "total_weight_value",
                            "total_weight_unit",
                            "normalized_weight_percent",
                            "child_count",
                            "counted_child_count",
                            "per_item_weight_value",
                            "per_item_weight_unit",
                            "weight_scheme",
                            "weight_evidence",
                            "policy_notes",
                            "recurrence_start_date",
                            "recurrence_due_time",
                            "recurrence_interval_days",
                            "recurrence_confidence",
                            "confidence",
                            "source_excerpt",
                        ],
                        "additionalProperties": False,
                    },
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "component": {"type": "string"},
                            "due_date": {"type": "string"},
                            "due_time": {"type": "string"},
                            "due_status": {"type": "string"},
                            "weight_percent": {"type": "string"},
                            "raw_weight_value": {"type": "string"},
                            "raw_weight_unit": {"type": "string"},
                            "weight_source": {"type": "string"},
                            "parent_category": {"type": "string"},
                            "parent_total_weight_value": {"type": "string"},
                            "parent_total_weight_unit": {"type": "string"},
                            "child_index": {"type": "integer"},
                            "child_count": {"type": "integer"},
                            "policy_notes": {"type": "string"},
                            "ungraded": {"type": "boolean"},
                            "status": {"type": "string"},
                            "notes": {"type": "string"},
                            "confidence": {"type": "number"},
                            "source_excerpt": {"type": "string"},
                        },
                        "required": [
                            "item",
                            "component",
                            "due_date",
                            "due_time",
                            "due_status",
                            "weight_percent",
                            "raw_weight_value",
                            "raw_weight_unit",
                            "weight_source",
                            "parent_category",
                            "parent_total_weight_value",
                            "parent_total_weight_unit",
                            "child_index",
                            "child_count",
                            "policy_notes",
                            "ungraded",
                            "status",
                            "notes",
                            "confidence",
                            "source_excerpt",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["course_suggestion", "grading_context", "categories", "warnings", "rows"],
            "additionalProperties": False,
        }

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You extract syllabus task data for a grade tracker. "
                            "Return grading_context, parent categories, real trackable child tasks, and brief warnings. "
                            "Do not invent structure, recurring discussion counts, or task weights without syllabus evidence. "
                            "Preserve repeated counts, raw grading units, recurrence, counted graded-item counts, and weight-scheme evidence when the syllabus explicitly states them."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_file",
                                "filename": os.path.basename(path),
                                "file_data": f"data:{mime_type};base64,{encoded}",
                            },
                        ],
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "syllabus_extraction",
                        "schema": schema,
                        "strict": True,
                    }
                },
                timeout=SYLLABUS_OPENAI_TIMEOUT_SECONDS,
            )
            payload = json.loads(response.output_text)
        except Exception as exc:
            raise SyllabusExtractionError(str(exc)) from exc
        finally:
            try:
                client.close()
            except Exception:
                pass
            self._client = None

        if not isinstance(payload, dict):
            raise SyllabusExtractionError("OpenAI returned an unexpected syllabus extraction format.")

        warnings_raw = payload.get("warnings", [])
        warnings = [str(x).strip() for x in warnings_raw if str(x).strip()] if isinstance(warnings_raw, list) else []

        grading_context = _sanitize_context(payload.get("grading_context", {}))
        categories_raw = payload.get("categories", [])
        if not isinstance(categories_raw, list):
            raise SyllabusExtractionError("OpenAI returned syllabus categories in an unexpected format.")
        categories = [_sanitize_category(category) for category in categories_raw]

        rows_raw = payload.get("rows", [])
        if not isinstance(rows_raw, list):
            raise SyllabusExtractionError("OpenAI returned syllabus rows in an unexpected format.")

        rows = _dedupe_rows([_sanitize_row(row, self.default_status) for row in rows_raw if isinstance(row, dict)])
        _apply_weight_normalization(rows, categories, grading_context)
        rows = _apply_recurrence_generation(rows, categories, grading_context, warnings)
        _apply_weight_normalization(rows, categories, grading_context)
        rows, removed_count = filter_deliverable_rows(rows)
        if removed_count:
            _append_warning_once(
                warnings,
                f"Filtered out {removed_count} rubric or marking-criteria row(s); kept assignment-level deliverables only.",
            )
        rows = _dedupe_rows(rows)
        for message in build_syllabus_validation_warnings(rows, categories=categories, grading_context=grading_context):
            _append_warning_once(warnings, message)
        if not rows:
            raise SyllabusExtractionError("AI extraction completed, but no trackable tasks were found.")

        return SyllabusExtractionResult(
            mode="ai",
            provider="openai",
            course_suggestion=str(payload.get("course_suggestion", "") or "").strip(),
            grading_context=grading_context,
            categories=categories,
            warnings=warnings,
            rows=rows,
        )


class SyllabusImportCoordinator:
    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_SYLLABUS_AI_MODEL,
        default_status: str = "not started",
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_SYLLABUS_AI_MODEL).strip() or DEFAULT_SYLLABUS_AI_MODEL
        self.default_status = normalize_status(default_status)
        self._extractor = OpenAISyllabusExtractor(
            api_key=self.api_key,
            model=self.model,
            default_status=self.default_status,
        )

    def extract(self, path: str) -> SyllabusExtractionResult:
        return self._extractor.extract(path)

    def cancel(self) -> None:
        self._extractor.cancel()
