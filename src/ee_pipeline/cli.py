"""Typer CLI for running the full pipeline or an individual module."""

from __future__ import annotations

from pathlib import Path

import typer

from .config import PipelineConfig
from .io_utils import read_jsonl, read_jsonl_models
from .models import ChunkedBlock, ClassificationResult, DocumentBlock, LLMInputRecord, TranslationResult

app = typer.Typer(name="ee-pipeline", help="Automotive E/E Requirements Extraction Pipeline")


def _stage_config(input_dir: Path = Path("data/input"), output_dir: Path = Path("output")) -> PipelineConfig:
    """Use one stable output directory for chained standalone commands."""
    return PipelineConfig(input_dir=input_dir, output_dir=output_dir, _run_dir=output_dir)


def _require(path: Path) -> Path:
    if not path.exists():
        raise typer.BadParameter(f"Required artifact not found: {path}")
    return path


@app.command()
def run(
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i", help="Input document directory"),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o", help="Output directory"),
) -> None:
    """Run M1 through M8 in a timestamped run directory."""
    from .orchestrator import run_pipeline

    summary = run_pipeline(PipelineConfig(input_dir=input_dir, output_dir=output_dir))
    if summary["status"] == "completed":
        typer.echo(f"\nReport saved to: {summary['report_path']}")
    else:
        typer.echo("Pipeline did not complete successfully.")


@app.command()
def m1(
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i"),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
) -> None:
    """Run M1: document loader."""
    from .modules.module1_loader import run as run_m1

    manifest = run_m1(_stage_config(input_dir, output_dir))
    typer.echo(f"Discovered {len(manifest)} files.")


@app.command()
def m2(
    input_dir: Path = typer.Option(Path("data/input"), "--input", "-i"),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o"),
) -> None:
    """Run M2: document parser."""
    from .modules.module2_parser import run as run_m2

    config = _stage_config(input_dir, output_dir)
    manifest = read_jsonl(_require(output_dir / "manifest.jsonl"))
    blocks = run_m2(config, manifest)
    typer.echo(f"Parsed {len(blocks)} blocks.")


@app.command()
def m25(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M2.5: keyword pre-screening."""
    from .modules.module2_5_prescreen import run as run_m25

    config = _stage_config(output_dir=output_dir)
    blocks = read_jsonl_models(_require(output_dir / "document_blocks.jsonl"), DocumentBlock)
    manifest = read_jsonl(output_dir / "manifest.jsonl")
    passed, skipped = run_m25(config, blocks, manifest)
    typer.echo(f"Passed: {len(passed)}, Skipped: {len(skipped)}")


@app.command()
def m3(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M3: contextual chunker."""
    from .modules.module3_chunker import run as run_m3

    config = _stage_config(output_dir=output_dir)
    blocks = read_jsonl_models(_require(output_dir / "document_blocks.jsonl"), DocumentBlock)
    chunks = run_m3(config, blocks)
    typer.echo(f"Created {len(chunks)} chunks.")


@app.command()
def m4(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M4: requirement segmenter."""
    from .modules.module4_segmenter import run as run_m4

    config = _stage_config(output_dir=output_dir)
    chunks = read_jsonl_models(_require(output_dir / "structured_blocks.jsonl"), ChunkedBlock)
    batches = run_m4(config, chunks)
    typer.echo(f"Created {len(batches)} requirement batches.")


@app.command(name="m45")
def m45(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M4.5: create prompt-ready records with stable CuR IDs."""
    from .modules.module4_5_llm_input import run as run_m45

    config = _stage_config(output_dir=output_dir)
    batches = read_jsonl(_require(output_dir / "requirement_batches.jsonl"))
    records = run_m45(config, batches)
    typer.echo(f"Created {len(records)} LLM input records.")


@app.command()
def m5(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M5: AI classification."""
    from .modules.module4_segmenter import segment_chunks
    from .modules.module5_classifier import classify_batch

    config = _stage_config(output_dir=output_dir)
    llm_inputs_path = output_dir / "llm_inputs.jsonl"
    batches_path = output_dir / "requirement_batches.jsonl"
    if llm_inputs_path.exists():
        batches = read_jsonl_models(llm_inputs_path, LLMInputRecord)
    elif batches_path.exists():
        batches = read_jsonl(batches_path)
    else:
        chunks = read_jsonl_models(_require(output_dir / "structured_blocks.jsonl"), ChunkedBlock)
        batches = segment_chunks(chunks)
    results = classify_batch(
        [item.model_dump(mode="json") if isinstance(item, LLMInputRecord) else item for item in batches],
        config,
    )
    from .io_utils import write_jsonl

    write_jsonl(output_dir / "classifications.jsonl", results)
    typer.echo(f"Classified {len(results)} batches.")


@app.command()
def m6(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M6: translation."""
    from .modules.module6_translate import translate_batch

    config = _stage_config(output_dir=output_dir)
    classifications = read_jsonl_models(_require(output_dir / "classifications.jsonl"), ClassificationResult)
    results = translate_batch(classifications, config)
    from .io_utils import write_jsonl

    write_jsonl(output_dir / "translated.jsonl", results)
    typer.echo(f"Translated {len(results)} requirements.")


@app.command()
def m7_m8(output_dir: Path = typer.Option(Path("output"), "--output", "-o")) -> None:
    """Run M7 aggregation and M8 report generation from standalone artifacts."""
    from .modules.module2_5_prescreen import load_results
    from .modules.module7_aggregator import run as run_m7
    from .modules.module8_report import generate_report
    from .orchestrator import _build_provenance_map

    config = _stage_config(output_dir=output_dir)
    manifest = read_jsonl(_require(output_dir / "manifest.jsonl"))
    classifications = read_jsonl_models(_require(output_dir / "classifications.jsonl"), ClassificationResult)
    translations = read_jsonl_models(_require(output_dir / "translated.jsonl"), TranslationResult)
    prescreen_results = load_results(output_dir)
    llm_inputs_path = output_dir / "llm_inputs.jsonl"
    batches = read_jsonl(llm_inputs_path if llm_inputs_path.exists() else output_dir / "requirement_batches.jsonl")
    keyword_map = {
        result.doc_id: [match.keyword for match in result.matched_keywords]
        for result in prescreen_results
    }
    provenance_map = _build_provenance_map(batches, keyword_map)
    records = run_m7(config, classifications, translations, provenance_map)
    report_path = generate_report(config, manifest, prescreen_results, classifications, records)
    typer.echo(f"Report generated: {report_path}")


if __name__ == "__main__":
    app()
