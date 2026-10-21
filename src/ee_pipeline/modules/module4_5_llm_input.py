"""M4.5: normalize M4 batches into prompt-ready, traceable LLM records."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import LLMInputRecord

_BRACKETED_ID = re.compile(r"\[([A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+)\]")
_LABELED_REQUIREMENT = re.compile(
    r"(?:^|\|\s*)(?:requirement(?:\s+text)?|description)\s*:\s*"
    r"(.+?)(?=\s*\|\s*[A-Za-z][A-Za-z0-9 _./()\-]{0,40}:|$)",
    re.IGNORECASE | re.DOTALL,
)
_AUTOSAR_DESCRIPTION = re.compile(
    r"Type::\s*Description:\s*\|\s*valid:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _source_text(batch: Mapping[str, Any]) -> str:
    """Recover source text without synthetic M4 heading/table prompt labels."""
    segments = batch.get("source_segments") or []
    values = [
        str(segment.get("segment_text", "")).strip()
        for segment in segments
        if str(segment.get("segment_text", "")).strip()
    ]
    if values:
        return "\n\n".join(values)
    text = str(batch.get("merged_text", "")).strip()
    return re.sub(r"^\[(?:Heading|Table):[^\]]+\]\s*", "", text, flags=re.MULTILINE).strip()


def _primary_text(source_text: str, batch: Mapping[str, Any]) -> str:
    """Prefer an explicit requirement/description field for reader-facing output."""
    for segment in batch.get("source_segments") or []:
        segment_text = str(segment.get("segment_text", "")).strip()
        match = _AUTOSAR_DESCRIPTION.search(segment_text)
        if match:
            return " ".join(match.group(1).split())
        match = _LABELED_REQUIREMENT.search(segment_text)
        if match and match.group(1).strip(" |"):
            return " ".join(match.group(1).strip(" |").split())
    return source_text


def _record_id(batch: Mapping[str, Any]) -> str | None:
    direct = str(batch.get("record_id") or "").strip()
    if direct:
        return direct
    values = {
        str(segment.get("record_id") or "").strip()
        for segment in batch.get("source_segments") or []
        if str(segment.get("record_id") or "").strip()
    }
    return next(iter(values)) if len(values) == 1 else None


def _heading_id(batch: Mapping[str, Any]) -> str | None:
    match = _BRACKETED_ID.search(str(batch.get("heading") or ""))
    return match.group(1) if match else None


def _fallback_prefix(doc_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", doc_id).strip("-")
    return value or "DOC"


def _is_table_context_row(text: str) -> bool:
    normalized = " ".join(text.split())
    if not normalized:
        return True
    if re.match(r"^Table\s+\d+", normalized, re.IGNORECASE):
        return True
    if normalized.startswith("Section:") and "|" not in normalized:
        return True
    return "|" not in normalized


def _semantic_prefix(text: str) -> str | None:
    fields = [field.strip() for field in text.split("|")]
    selected = [
        field
        for field in fields
        if re.match(r"^(?:Symbol|Parameter|Feature|Component):", field, re.IGNORECASE)
    ]
    return " | ".join(selected) if selected else None


def _table_units(batch: Mapping[str, Any]) -> list[tuple[str, str, list[dict], list[str]]]:
    """Split generic specification tables into rows while retaining table context."""
    parent_batch_id = str(batch.get("batch_id", ""))
    segments = [dict(segment) for segment in batch.get("source_segments") or []]
    if (
        batch.get("candidate_type") != "spec_table"
        or _record_id(batch)
        or _heading_id(batch)
        or len(segments) <= 1
    ):
        return [(parent_batch_id, _source_text(batch), segments, [])]

    units: list[tuple[str, str, list[dict], list[str]]] = []
    table_context: str | None = None
    condition_context: str | None = None
    section_context: str | None = None
    inherited_semantics: str | None = None
    for unit_index, segment in enumerate(segments, start=1):
        text = str(segment.get("segment_text", "")).strip()
        if not text:
            continue
        if _is_table_context_row(text):
            normalized = " ".join(text.split())
            if re.match(r"^Table\s+\d+", normalized, re.IGNORECASE):
                table_context = normalized
            elif normalized.startswith("Section:"):
                section_context = normalized
            else:
                condition_context = normalized
            continue

        prefix = _semantic_prefix(text)
        row_context = [
            value
            for value in (table_context, condition_context, section_context)
            if value
        ]
        original_text = text
        if inherited_semantics and not re.search(r"\b(?:Symbol|Parameter|Feature|Component):", text, re.IGNORECASE):
            original_text = f"{inherited_semantics} | {text}"
            row_context.append(f"Inherited fields: {inherited_semantics}")
        if prefix:
            inherited_semantics = prefix
        units.append(
            (
                f"{parent_batch_id}__r{unit_index:04d}",
                original_text,
                [segment],
                row_context,
            )
        )
    return units or [(parent_batch_id, _source_text(batch), segments, [])]


def prepare_llm_inputs(batches: list[Mapping[str, Any]]) -> list[LLMInputRecord]:
    """Create deterministic M4.5 records without document-specific content rules."""
    counters: dict[str, int] = defaultdict(int)
    records: list[LLMInputRecord] = []
    for batch in batches:
        if batch.get("skip_candidate", False):
            continue
        doc_id = str(batch.get("document_id") or batch.get("doc_id") or "unknown")
        record_id = _record_id(batch)
        heading = str(batch.get("heading") or "").strip() or None
        source_file = str(batch.get("source_file") or "")
        page_numbers = [int(value) for value in batch.get("page_numbers") or [] if value is not None]
        parent_batch_id = str(batch.get("batch_id", ""))
        units = _table_units(batch)
        for child_batch_id, source_text, source_segments, inherited_context in units:
            counters[doc_id] += 1
            cur_id = record_id or _heading_id(batch)
            if not cur_id:
                cur_id = f"{_fallback_prefix(doc_id)}-CUR-{counters[doc_id]:04d}"
            original_text = (
                _primary_text(source_text, {**batch, "source_segments": source_segments})
                if child_batch_id == parent_batch_id
                else source_text
            )
            unit_pages = list(
                dict.fromkeys(
                    int(segment["page_number"])
                    for segment in source_segments
                    if segment.get("page_number") is not None
                )
            ) or page_numbers
            context = [
                f"Document: {source_file or doc_id}",
                f"Customer requirement ID: {cur_id}",
                f"Candidate type: {batch.get('candidate_type', 'paragraph')}",
            ]
            if heading:
                context.append(f"Heading: {heading}")
            if unit_pages:
                context.append("Source pages/slides: " + ", ".join(map(str, unit_pages)))
            if batch.get("table_title"):
                context.append(f"Table: {batch['table_title']}")
            context.extend(f"Table context: {value}" for value in inherited_context)
            context.append("Source text:\n" + source_text)

            records.append(
                LLMInputRecord(
                    batch_id=child_batch_id,
                    parent_batch_id=parent_batch_id,
                    doc_id=doc_id,
                    cur_id=cur_id,
                    heading=heading,
                    candidate_type=str(batch.get("candidate_type", "paragraph")),
                    original_text=original_text,
                    llm_text="\n".join(context),
                    source_file=source_file,
                    page_numbers=unit_pages,
                    table_id=batch.get("table_id"),
                    table_title=batch.get("table_title"),
                    record_id=record_id,
                    source_unit_id=batch.get("source_unit_id"),
                    source_segments=source_segments,
                )
            )
    return records


def run(config: PipelineConfig, batches: list[Mapping[str, Any]]) -> list[LLMInputRecord]:
    """Execute M4.5 and persist ``llm_inputs.jsonl``."""
    config.prepare_run_dir()
    records = prepare_llm_inputs(batches)
    path = write_jsonl(config.run_dir / "llm_inputs.jsonl", records)
    print(f"[M4.5] {len(records)} prompt-ready records → {path}")
    return records
