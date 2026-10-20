"""CLI entry point for the E/E Requirements Pipeline."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(name="ee-pipeline", help="Automotive E/E Requirements Extraction Pipeline")


@app.command()
def run(
    input_dir: Path = typer.Option(
        Path("data/input"),
        "--input", "-i",
        help="Directory containing input documents",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output", "-o",
        help="Directory for output files",
    ),
):
    """Run the full E/E requirements extraction pipeline."""
    from .config import PipelineConfig
    from .orchestrator import run_pipeline

    config = PipelineConfig(input_dir=input_dir, output_dir=output_dir)
    summary = run_pipeline(config)

    if summary["status"] == "completed":
        typer.echo(f"\nReport saved to: {summary['report_path']}")
    else:
        typer.echo("Pipeline did not complete successfully.")


@app.command()
def m1(
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i"),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M1 only: Document Loader."""
    from .config import PipelineConfig
    from .modules.module1_loader import run

    config = PipelineConfig(input_dir=input_dir, output_dir=output_dir)
    manifest = run(config)
    typer.echo(f"Discovered {len(manifest)} files.")


@app.command()
def m2(
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i"),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M2 only: Document Parser."""
    import json
    from .config import PipelineConfig
    from .modules.module2_parser import run

    config = PipelineConfig(input_dir=input_dir, output_dir=output_dir)

    # Load manifest from M1
    manifest_path = output_dir / "manifest.jsonl"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f]

    blocks = run(config, manifest)
    typer.echo(f"Parsed {len(blocks)} blocks.")


@app.command()
def m25(
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M2.5 only: Keyword Pre-screening."""
    import json
    from .config import PipelineConfig
    from .modules.module2_5_prescreen import run

    config = PipelineConfig(output_dir=output_dir)

    # Load blocks from M2
    blocks_path = output_dir / "document_blocks.jsonl"
    with open(blocks_path, "r", encoding="utf-8") as f:
        from .models import DocumentBlock
        blocks = [DocumentBlock.model_validate_json(line) for line in f]

    pass_blocks, skip_blocks = run(config, blocks)
    typer.echo(f"Passed: {len(pass_blocks)}, Skipped: {len(skip_blocks)}")


@app.command()
def m5(
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M5 only: AI Classification (ChatT)."""
    import json
    from .config import PipelineConfig
    from .modules.module4_segmenter import segment_chunks
    from .modules.module5_classifier import classify_batch

    config = PipelineConfig(output_dir=output_dir)

    # Load chunks from M3
    chunks_path = output_dir / "structured_blocks.jsonl"
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = [json.loads(line) for line in f]

    segments = segment_chunks(chunks)
    classifications = classify_batch(segments)

    # Save results
    from .models import ClassificationResult
    results_path = output_dir / "classifications.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in classifications:
            f.write(r.model_dump_json() + "\n")

    typer.echo(f"Classified {len(classifications)} segments.")


@app.command()
def m6(
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M6 only: Translation (ChatT)."""
    import json
    from .config import PipelineConfig
    from .modules.module6_translate import translate_batch
    from .models import ClassificationResult

    config = PipelineConfig(output_dir=output_dir)

    # Load classifications from M5
    cls_path = output_dir / "classifications.jsonl"
    with open(cls_path, "r", encoding="utf-8") as f:
        classifications = [ClassificationResult.model_validate_json(line) for line in f]

    translations = translate_batch(classifications)

    # Save results
    results_path = output_dir / "translated.jsonl"
    with open(results_path, "w", encoding="utf-8") as f:
        for r in translations:
            f.write(r.model_dump_json() + "\n")

    typer.echo(f"Translated {len(translations)} requirements.")


@app.command()
def m7_m8(
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
):
    """Run M7+M8: Aggregation and Report generation."""
    import json
    from .config import PipelineConfig
    from .modules.module7_aggregator import aggregate
    from .modules.module8_report import generate_report
    from .models import ClassificationResult, TranslationResult

    config = PipelineConfig(output_dir=output_dir)

    # Load from previous modules
    with open(output_dir / "manifest.jsonl", "r") as f:
        manifest = [json.loads(line) for line in f]

    with open(output_dir / "classifications.jsonl", "r") as f:
        classifications = [ClassificationResult.model_validate_json(line) for line in f]

    with open(output_dir / "translated.jsonl", "r") as f:
        translations = [TranslationResult.model_validate_json(line) for line in f]

    spec_records = aggregate(classifications, translations)
    report_path = generate_report(config, manifest, [], classifications, spec_records)

    typer.echo(f"Report generated: {report_path}")


if __name__ == "__main__":
    app()