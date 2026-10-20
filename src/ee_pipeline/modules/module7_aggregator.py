"""M7: Aggregator — combines classification + translation into final spec records.

Merges M5 (classification) and M6 (translation) results into unified SpecRecord objects.
"""

from __future__ import annotations

from pathlib import Path

from ..config import PipelineConfig
from ..models import ClassificationResult, SpecRecord, TranslationResult


def aggregate(
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
) -> list[SpecRecord]:
    """Aggregate classification and translation results into spec records.

    Only includes records where is_requirement=True AND is_ee_related=True.

    Args:
        classifications: M5 output.
        translations: M6 output.

    Returns:
        List of SpecRecord objects.
    """
    # Build translation lookup
    translation_map: dict[str, TranslationResult] = {}
    for t in translations:
        translation_map[t.block_id] = t

    records: list[SpecRecord] = []

    for cls in classifications:
        # Only EE requirements (intersection of both axes)
        if not (cls.is_requirement and cls.is_ee_related):
            continue

        translation = translation_map.get(cls.block_id)

        records.append(SpecRecord(
            doc_id=cls.doc_id,
            block_id=cls.block_id,
            heading="",  # Will be filled from source data if available
            english_text=cls.original_content,
            korean_translation=translation.translated_text if translation else "",
            is_requirement=cls.is_requirement,
            is_ee_related=cls.is_ee_related,
            ee_category=cls.ee_category.value if cls.ee_category else None,
            matched_keywords=[],  # Will be filled by caller
            source_file="",  # Will be filled by caller
            page_number=None,  # Will be filled by caller
        ))

    return records


def run(
    config: PipelineConfig,
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
) -> list[SpecRecord]:
    """Execute M7: aggregate results.

    Returns list of SpecRecord objects.
    Writes spec_records.jsonl to output.
    """
    records = aggregate(classifications, translations)

    records_path = config.run_dir / "spec_records.jsonl"
    with open(records_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    print(f"[M7] {len(records)} EE requirement records → {records_path}")
    return records