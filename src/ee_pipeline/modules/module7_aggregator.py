"""M7: combine classification, translation, and source provenance."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from ..config import PipelineConfig
from ..io_utils import write_jsonl
from ..models import ClassificationResult, SpecRecord, TranslationResult


def _provenance_for(block_id: str, provenance_map: Mapping[str, Mapping]) -> Mapping:
    """Resolve a batch or segment key without assuming one producer format."""
    return provenance_map.get(block_id, {})


def aggregate(
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
    provenance_map: Mapping[str, Mapping] | None = None,
) -> list[SpecRecord]:
    """Create final records for requirement + E/E classifications only."""
    selected = [
        classification
        for classification in classifications
        if classification.is_requirement and classification.is_ee_related
    ]
    duplicate_translation_ids = sorted(
        block_id
        for block_id, count in Counter(translation.block_id for translation in translations).items()
        if count > 1
    )
    if duplicate_translation_ids:
        sample = ", ".join(duplicate_translation_ids[:5])
        raise ValueError(f"M7 received duplicate translation IDs: {sample}")

    translations_by_id = {translation.block_id: translation for translation in translations}
    missing_translation_ids = sorted(
        classification.block_id
        for classification in selected
        if classification.block_id not in translations_by_id
    )
    if missing_translation_ids:
        sample = ", ".join(missing_translation_ids[:5])
        raise ValueError(
            f"M7 is missing translations for {len(missing_translation_ids)} E/E requirement(s): {sample}"
        )

    invalid_translation_ids = sorted(
        classification.block_id
        for classification in selected
        if (
            translations_by_id[classification.block_id].status != "OK"
            or not translations_by_id[classification.block_id].translated_text.strip()
        )
    )
    if invalid_translation_ids:
        sample = ", ".join(invalid_translation_ids[:5])
        raise ValueError(
            f"M7 received unusable translations for {len(invalid_translation_ids)} E/E requirement(s): {sample}"
        )

    provenance = provenance_map or {}
    records: list[SpecRecord] = []

    for classification in selected:
        source = _provenance_for(classification.block_id, provenance)
        translation = translations_by_id[classification.block_id]
        matched_keywords = source.get("matched_keywords", [])
        records.append(
            SpecRecord(
                doc_id=classification.doc_id,
                block_id=classification.block_id,
                cur_id=classification.cur_id or source.get("cur_id") or classification.block_id,
                heading=source.get("heading") or source.get("heading_text"),
                english_text=classification.original_content,
                korean_translation=translation.translated_text,
                is_requirement=True,
                is_ee_related=True,
                ee_category=classification.ee_category.value if classification.ee_category else None,
                notes=classification.reasoning,
                matched_keywords=[str(keyword) for keyword in matched_keywords],
                source_file=source.get("source_file", ""),
                page_number=source.get("page_number"),
                heading_index=source.get("heading_index"),
                source_start_line=source.get("source_start_line"),
                source_end_line=source.get("source_end_line"),
                table_id=source.get("table_id"),
                record_id=source.get("record_id"),
            )
        )
    return records


def run(
    config: PipelineConfig,
    classifications: list[ClassificationResult],
    translations: list[TranslationResult],
    provenance_map: Mapping[str, Mapping] | None = None,
) -> list[SpecRecord]:
    """Execute M7 and write ``spec_records.jsonl``."""
    config.prepare_run_dir()
    records = aggregate(classifications, translations, provenance_map)
    path = write_jsonl(config.run_dir / "spec_records.jsonl", records)
    print(f"[M7] {len(records)} EE requirement records → {path}")
    return records
