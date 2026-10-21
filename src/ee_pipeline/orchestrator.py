"""Pipeline orchestrator: execute M1 through M8 with shared run state."""

from __future__ import annotations

import asyncio
import json

from .config import PipelineConfig


def run_pipeline(config: PipelineConfig | None = None) -> dict:
    """Run the complete pipeline synchronously and return a summary."""
    return asyncio.run(_run_pipeline(config or PipelineConfig()))


def _build_provenance_map(batches: list[dict], keyword_map: dict[str, list[str]]) -> dict[str, dict]:
    """Index both batch IDs and source segment IDs for M7 traceability."""
    provenance: dict[str, dict] = {}
    for batch in batches:
        source_segments = batch.get("source_segments", [])
        if not source_segments:
            continue
        first = source_segments[0]
        doc_id = batch.get("doc_id") or batch.get("document_id", "")
        page_numbers = batch.get("page_numbers") or []
        common = {
            "cur_id": batch.get("cur_id"),
            "heading": batch.get("heading") or first.get("heading_text"),
            "source_file": batch.get("source_file") or first.get("source_file") or doc_id,
            "page_number": page_numbers[0] if page_numbers else first.get("page_number"),
            "heading_index": first.get("heading_index"),
            "source_start_line": first.get("source_start_line"),
            "source_end_line": first.get("source_end_line"),
            "table_id": batch.get("table_id") or first.get("table_id"),
            "record_id": batch.get("record_id") or first.get("record_id"),
            "matched_keywords": keyword_map.get(doc_id, []),
        }
        provenance[batch.get("batch_id", "")] = common
        for source in source_segments:
            provenance[source.get("segment_id", "")] = {
                **common,
                "heading": source.get("heading_text") or common["heading"],
                "heading_index": source.get("heading_index"),
                "source_file": source.get("source_file") or common["source_file"],
                "page_number": source.get("page_number") or common["page_number"],
                "source_start_line": source.get("source_start_line"),
                "source_end_line": source.get("source_end_line"),
                "table_id": source.get("table_id") or common["table_id"],
                "record_id": source.get("record_id") or common["record_id"],
            }
    return provenance


async def _run_pipeline(config: PipelineConfig) -> dict:
    """Async implementation shared by the CLI and library entry point."""
    config.prepare_run_dir()

    from .modules.module1_loader import run as m1_run
    from .modules.module2_5_prescreen import load_results as load_prescreen_results
    from .modules.module2_5_prescreen import run as m25_run
    from .modules.module2_parser import run as m2_run
    from .modules.module3_chunker import run as m3_run
    from .modules.module4_5_llm_input import run as m45_run
    from .modules.module4_segmenter import run as m4_run
    from .modules.module5_classifier import run as m5_run
    from .modules.module6_translate import run as m6_run
    from .modules.module7_aggregator import run as m7_run
    from .modules.module8_report import run as m8_run

    manifest = m1_run(config)
    if not manifest:
        print("[PIPELINE] No input files found. Place files in:", config.input_dir)
        summary = {"status": "no_files", "manifest": []}
        (config.run_dir / "pipeline_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return summary

    all_blocks = m2_run(config, manifest)
    pass_blocks, skip_blocks = m25_run(config, all_blocks, manifest)
    prescreen_results = load_prescreen_results(config.run_dir)
    # M2.5 is evidence/prioritization only. Every parsed block must reach M5 so
    # the denominator includes non-E/E requirements as well.
    chunks = m3_run(config, all_blocks)
    batches = m4_run(config, chunks)
    llm_inputs = m45_run(config, batches)
    classifications = await m5_run(config, llm_inputs)
    translations = await m6_run(config, classifications)

    keyword_map = {
        result.doc_id: [match.keyword for match in result.matched_keywords]
        for result in prescreen_results
    }
    provenance_map = _build_provenance_map(
        [item.model_dump(mode="json") for item in llm_inputs],
        keyword_map,
    )
    spec_records = m7_run(config, classifications, translations, provenance_map)
    report_path = m8_run(config, manifest, prescreen_results, classifications, spec_records)

    summary = {
        "status": "completed",
        "total_files": len(manifest),
        "total_blocks": len(all_blocks),
        "blocks_passed_filter": len(pass_blocks),
        "blocks_skipped_filter": len(skip_blocks),
        "chunks": len(chunks),
        "batches": len(batches),
        "llm_inputs": len(llm_inputs),
        "classifications": len(classifications),
        "ee_requirements": sum(item.is_requirement and item.is_ee_related for item in classifications),
        "translations": len(translations),
        "spec_records": len(spec_records),
        "report_path": str(report_path),
        "run_dir": str(config.run_dir),
    }
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("=" * 60)
    (config.run_dir / "pipeline_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary
