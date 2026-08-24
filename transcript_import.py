from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass, field

try:
    from openai import OpenAI as OpenAIClient  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenAIClient = None

from syllabus_import import DEFAULT_SYLLABUS_AI_MODEL, SYLLABUS_OPENAI_TIMEOUT_SECONDS


TRANSCRIPT_TERM_VALUES = {"fall", "winter", "summer"}
TRANSCRIPT_TERM_ALIASES = {
    "autumn": "fall",
    "fall": "fall",
    "f": "fall",
    "winter": "winter",
    "spring": "winter",
    "spr": "winter",
    "sp": "winter",
    "w": "winter",
    "summer": "summer",
    "sum": "summer",
    "su": "summer",
    "s": "summer",
}


@dataclass
class TranscriptCourseRow:
    course_name: str = ""
    final_grade: str = ""
    credit_hours: float = 0.0
    term: str = "fall"
    academic_year_start: int = 0
    confidence: float = 1.0
    source_excerpt: str = ""


@dataclass
class TranscriptExtractionResult:
    mode: str = "ai"
    provider: str = "openai"
    institution: str = ""
    student_name: str = ""
    warnings: list[str] = field(default_factory=list)
    rows: list[TranscriptCourseRow] = field(default_factory=list)


class TranscriptExtractionError(RuntimeError):
    pass


def transcript_openai_available() -> bool:
    return OpenAIClient is not None


def normalize_transcript_term(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in TRANSCRIPT_TERM_VALUES:
        return raw
    return TRANSCRIPT_TERM_ALIASES.get(raw, "fall")


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except Exception:
        return 1.0
    return max(0.0, min(1.0, confidence))


def _normalize_year_start(value: object) -> int:
    try:
        year = int(float(str(value or "").strip()))
    except Exception:
        return 0
    return year if 1900 <= year <= 2200 else 0


def _sanitize_row(row: object) -> TranscriptCourseRow | None:
    if not isinstance(row, dict):
        return None

    course_name = str(row.get("course_name", "")).strip()
    final_grade = str(row.get("final_grade", "") or "").strip()
    try:
        credit_hours = float(row.get("credit_hours"))
    except Exception:
        return None

    if not course_name or credit_hours <= 0 or not final_grade:
        return None

    try:
        numeric_grade = float(final_grade.replace("%", "").strip())
    except Exception:
        numeric_grade = None
    if numeric_grade is not None and (numeric_grade < 0 or numeric_grade > 100):
        return None

    academic_year_start = _normalize_year_start(row.get("academic_year_start"))
    if academic_year_start <= 0:
        return None

    return TranscriptCourseRow(
        course_name=course_name,
        final_grade=final_grade,
        credit_hours=credit_hours,
        term=normalize_transcript_term(row.get("term")),
        academic_year_start=academic_year_start,
        confidence=_normalize_confidence(row.get("confidence")),
        source_excerpt=str(row.get("source_excerpt", "") or "").strip(),
    )


def _dedupe_rows(rows: list[TranscriptCourseRow]) -> list[TranscriptCourseRow]:
    seen: set[tuple[str, str, int, str, str]] = set()
    out: list[TranscriptCourseRow] = []
    for row in rows:
        key = (
            row.course_name.strip().lower(),
            normalize_transcript_term(row.term),
            int(row.academic_year_start),
            row.final_grade.strip().lower(),
            f"{float(row.credit_hours):.4f}",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def build_transcript_extraction_prompt() -> str:
    return """
Extract completed university or college courses from this transcript or academic record.

Return only historical completed courses that include all of the following:
- a course code or course title =
- an explicit final grade that is either a percentage or a letter grade (A-F with optional + or -), excluding non-final or administrative statuses like W, I, NG, AUD, IP, and similar
- explicit credit hours
- a term or semester
- the academic year start as an integer

Rules:
- `term` must be exactly one of: fall, winter, summer
- map spring terms to `winter`
- `academic_year_start` is the first year of the academic cycle
  Examples:
  - Fall 2025 -> academic_year_start = 2025
  - Winter 2026 in the 2025/2026 year -> academic_year_start = 2025
  - Summer 2026 in the 2025/2026 year -> academic_year_start = 2025
- `credit_hours` should be numeric and can include decimals
- `final_grade` should be the clean final grade text shown on the record, such as `89`, `89%`, `A`, or `B+`
- if the transcript only shows a letter grade, keep it as a letter grade and do not invent a percentage
- valid completed letter grades include standard evaluative grades such as `A+`, `A`, `A-`, `B+`, `B`, `B-`, `C+`, `C`, `C-`, `D`, `F`, `P`, or other clearly completed evaluative grades
- invalid non-final or administrative grades include `W`, `Withdrawn`, `I`, `INC`, `IP`, `In Progress`, `NG`, `No Grade`, `AUD`, `Audit`, `Current Program`, `Planned`, and similar statuses
- never treat `W` as a completed final grade
- if the grade is `W` or any other non-final administrative status, exclude the row even if credit hours and term are present
- do not invent courses, percentages, terms, or credit hours
- do not include courses that are in progress or planned for the future
- include transfer or external-institution courses if the row contains a course code, credit hours, a valid completed final grade, and term/year context, even if the title is missing or replaced by the institution name
- if a transfer/external row has a valid course code plus credit hours plus valid completed final grade, treat it as a real completed course, not as an institution summary
- only exclude a row as an institution summary if it is clearly a GPA line, institution GPA line, standings line, dean’s list line, awarded degree line, or another non-course summary
- if a course has a code but the title is unclear or missing, include it and use the available text as the title rather than excluding it
- if a row is ambiguous, exclude it and mention the issue in `warnings`
- keep repeated courses if they appear in different terms or academic years
- if a row contains `Repeat` plus a clearly separable valid completed final grade, keep the course with the final grade and add a warning if needed
- `source_excerpt` should be a short supporting snippet when possible
""".strip()


class OpenAITranscriptExtractor:
    provider = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_SYLLABUS_AI_MODEL) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_SYLLABUS_AI_MODEL).strip() or DEFAULT_SYLLABUS_AI_MODEL
        self._client = None

    def cancel(self) -> None:
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def extract(self, path: str) -> TranscriptExtractionResult:
        if OpenAIClient is None:
            raise TranscriptExtractionError("The OpenAI Python package is not installed.")
        if not self.api_key:
            raise TranscriptExtractionError("No OpenAI API key is configured.")

        try:
            client = OpenAIClient(
                api_key=self.api_key,
                timeout=SYLLABUS_OPENAI_TIMEOUT_SECONDS,
                max_retries=0,
            )
            self._client = client
            with open(path, "rb") as handle:
                raw = handle.read()
        except Exception as exc:
            self._client = None
            raise TranscriptExtractionError(str(exc)) from exc

        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        encoded = base64.b64encode(raw).decode("ascii")

        schema = {
            "type": "object",
            "properties": {
                "institution": {"type": "string"},
                "student_name": {"type": "string"},
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "course_name": {"type": "string"},
                            "final_grade": {"type": "string"},
                            "credit_hours": {"type": "number"},
                            "term": {"type": "string"},
                            "academic_year_start": {"type": "integer"},
                            "confidence": {"type": "number"},
                            "source_excerpt": {"type": "string"},
                        },
                        "required": [
                            "course_name",
                            "final_grade",
                            "credit_hours",
                            "term",
                            "academic_year_start",
                            "confidence",
                            "source_excerpt",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["institution", "student_name", "warnings", "rows"],
            "additionalProperties": False,
        }

        try:
            response = client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You extract previous-course data from transcripts or academic records for a GPA tracker. "
                            "Return only evidence-backed historical courses with explicit final grades and credit hours. "
                            "Do not guess or infer missing percentages. Preserve letter grades when the record only shows letters."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": build_transcript_extraction_prompt()},
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
                        "name": "transcript_extraction",
                        "schema": schema,
                        "strict": True,
                    }
                },
                timeout=SYLLABUS_OPENAI_TIMEOUT_SECONDS,
            )
            payload = json.loads(response.output_text)
        except Exception as exc:
            raise TranscriptExtractionError(str(exc)) from exc
        finally:
            self._client = None

        if not isinstance(payload, dict):
            raise TranscriptExtractionError("OpenAI returned an unexpected transcript extraction format.")

        warnings_raw = payload.get("warnings", [])
        warnings = [str(item).strip() for item in warnings_raw if str(item).strip()] if isinstance(warnings_raw, list) else []
        rows_raw = payload.get("rows", [])
        if not isinstance(rows_raw, list):
            raise TranscriptExtractionError("OpenAI returned transcript rows in an unexpected format.")

        rows = _dedupe_rows(
            [
                sanitized
                for item in rows_raw
                for sanitized in [_sanitize_row(item)]
                if sanitized is not None
            ]
        )
        if not rows:
            raise TranscriptExtractionError(
                "AI extraction completed, but no previous courses with usable grades were found."
            )

        return TranscriptExtractionResult(
            mode="ai",
            provider="openai",
            institution=str(payload.get("institution", "") or "").strip(),
            student_name=str(payload.get("student_name", "") or "").strip(),
            warnings=warnings,
            rows=rows,
        )


class TranscriptImportCoordinator:
    def __init__(self, api_key: str = "", model: str = DEFAULT_SYLLABUS_AI_MODEL) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or DEFAULT_SYLLABUS_AI_MODEL).strip() or DEFAULT_SYLLABUS_AI_MODEL
        self._extractor = OpenAITranscriptExtractor(api_key=self.api_key, model=self.model)

    def extract(self, path: str) -> TranscriptExtractionResult:
        return self._extractor.extract(path)

    def cancel(self) -> None:
        self._extractor.cancel()
