"""M7: Aggregator — combines classification + translation into final spec records.

Merges M5 (classification) and M6 (translation) results into unified SpecRecord objects.
Preserves provenance tracking from source documents.
"""

from __future__ import annotations

from pathlib import Path

from ..config import PipelineConfig
from ..models import ClassificationResult, SpecRecord, TranslationResult


def aggregate(
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
    provenance_map: dict[str, dict] | None = None,
) -> list[SpecRecord]:
    """Aggregate classification and translation results into spec records.

    Only includes records where is_requirement=True AND is_ee_related=True.

    Args:
        classifications: M5 output (one per batch).
        translations: M6 output.
        provenance_map: Optional mapping of segment_id to provenance info.

    Returns:
        List of SpecRecord objects with provenance tracking.
    """
    # Build translation lookup
    translation_map: dict[str, TranslationResult] = {}
    for t in translations:
        translation_map[t.block_id] = t

    # Build provenance lookup
    provenance_map = provenance_map or {}

    records: list[SpecRecord] = []

    for cls in classifications:
        # Only EE requirements (intersection of both axes)
        if not (cls.is_requirement and cls.is_ee_related):
            continue

        translation = translation_map.get(cls.block_id)

        # Try to find provenance from any source segment in the batch
        # cls.block_id is the batch_id, look for source segments
        provenance = provenance_map.get(cls.block_id, {})

        records.append(SpecRecord(
            doc_id=cls.doc_id,
            block_id=cls.block_id,
            heading=provenance.get("heading", ""),
            english_text=cls.original_content,
            korean_translation=translation.translated_text if translation else "",
            is_requirement=cls.is_requirement,
            is_ee_related=cls.is_ee_related,
            ee_category=cls.ee_category.value if cls.ee_category else None,
            matched_keywords=[],  # Will be filled by caller
            source_file=provenance.get("source_file", ""),
            page_number=provenance.get("page_number"),
            # Provenance tracking
            heading_index=provenance.get("heading_index"),
            source_start_line=provenance.get("source_start_line"),
            source_end_line=provenance.get("source_end_line"),
        ))

    return records


def run(
    config: PipelineConfig,
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
    provenance_map: dict[str, dict] | None = None,
) -> list[SpecRecord]:
    """Execute M7: aggregate results.

    Returns list of SpecRecord objects with provenance tracking.
    Writes spec_records.jsonl to output.
    """
    records = aggregate(classifications, translations, provenance_map)

    records_path = config.run_dir / "spec_records.jsonl"
    with open(records_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    print(f"[M7] {len(records)} EE requirement records → {records_path}")
    return records