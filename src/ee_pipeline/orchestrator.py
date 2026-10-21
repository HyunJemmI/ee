"""Pipeline orchestrator — chains all modules together."""

from __future__ import annotations

import json
from pathlib import Path

from .config import PipelineConfig
from .models import ClassificationResult, SpecRecord, TranslationResult


def run_pipeline(config: PipelineConfig | None = None) -> dict:
    """Run the full pipeline end-to-end.

    Returns a summary dict with statistics.
    """
    if config is None:
        config = PipelineConfig()

        run_dir = config.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

    # ---- M1: Document Loader ----
    from .modules.module1_loader import run as m1_run
    manifest = m1_run(config)
    if not manifest:
        print("[PIPELINE] No input files found. Place files in:", config.input_dir)
        return {"status": "no_files", "manifest": []}

    # ---- M2: Document Parser ----
    from .modules.module2_parser import run as m2_run
    all_blocks = m2_run(config, manifest)

    # ---- M2.5: Keyword Pre-screening ----
    from .modules.module2_5_prescreen import run as m25_run
    pass_blocks, skip_blocks = m25_run(config, all_blocks)

    # ---- M3: Contextual Chunker ----
    from .modules.module3_chunker import run as m3_run
    chunks = m3_run(config, pass_blocks)

    # ---- M4: Requirement Segmenter ----
    from .modules.module4_segmenter import run as m4_run
    segments = m4_run(config, chunks)

    # ---- M5: AI Classifier (ChatT) ----
    from .modules.module5_classifier import run as m5_run
    classifications = m5_run(config, segments)

    # ---- M6: Translate & Validate (ChatT) ----
    from .modules.module6_translate import run as m6_run
    translations = m6_run(config, classifications)

    # ---- M7: Aggregator ----
    from .modules.module7_aggregator import run as m7_run
    # Build provenance map from M4 batches' source_segments for M7
    provenance_map = {}
    for batch in segments:  # segments = M4 batches
        for src_seg in batch.get("source_segments", []):
            seg_id = src_seg.get("segment_id", "")
            if seg_id:
                provenance_map[seg_id] = {
                    "heading": src_seg.get("heading_text"),
                    "source_file": batch.get("document_id", ""),
                    "page_number": None,
                    "heading_index": src_seg.get("heading_index"),
                    "source_start_line": src_seg.get("source_start_line"),
                    "source_end_line": src_seg.get("source_end_line"),
                }
    spec_records = m7_run(config, classifications, translations, provenance_map)

    # ---- M8: XLSX Report Generator ----
    from .modules.module8_report import run as m8_run
    report_path = m8_run(config, manifest, [], classifications, spec_records)

    # ---- Summary ----
    summary = {
        "status": "completed",
        "total_files": len(manifest),
        "total_blocks": len(all_blocks),
        "blocks_passed_filter": len(pass_blocks),
        "blocks_skipped_filter": len(skip_blocks),
        "chunks": len(chunks),
        "segments": len(segments),
        "classifications": len(classifications),
        "ee_requirements": sum(1 for c in classifications if c.is_requirement and c.is_ee_related),
        "translations": len(translations),
        "spec_records": len(spec_records),
        "report_path": str(report_path),
    }

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 60)

    # Write summary
    summary_path = config.run_dir / "pipeline_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    return summary
