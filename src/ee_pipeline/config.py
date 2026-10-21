"""Configuration for the E/E Requirements Pipeline."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Project paths and local environment
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Resolve the file explicitly so CLI execution does not depend on the current
# working directory. Existing shell variables still take precedence.
load_dotenv(dotenv_path=ENV_FILE, override=False)


# ---------------------------------------------------------------------------
# ChatT API configuration
# ---------------------------------------------------------------------------

CHATT_API_KEY: str = os.getenv("CHATT_API_KEY", "").strip()
CHATT_BASE_URL: str = os.getenv("CHATT_BASE_URL", "").strip()
CHATT_MODEL: str = os.getenv("CHATT_MODEL", "").strip()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data"
KEYWORDS_DIR = DATA_DIR / "keywords"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

KEYWORDS_FILE = KEYWORDS_DIR / "ee_keywords.json"


# ---------------------------------------------------------------------------
# Keyword loading
# ---------------------------------------------------------------------------

def load_keywords() -> dict:
    """Load EE keywords from JSON file."""
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM_PROMPT = (
    "You are an automotive requirements analyst. "
    "Classify requirement status and automotive E/E relevance independently."
)

CLASSIFICATION_PROMPT = """Classify the following source record on TWO INDEPENDENT axes.

## Axis 1: Is this a requirement/specification?
A requirement/specification must be a complete, independently testable statement or
table row. It typically contains:
- "shall", "must", "should", "required", "required to", "shall not", "must not"
- Or defined product behavior, a parameter plus value/limit/unit, an interface or pin
  function, register behavior, a mandatory condition, or another verifiable specification
- A non-E/E requirement must still be marked as a requirement

Do not classify ordering codes, part numbers, package names, product marking rows,
headings, figure captions, bare signal/component labels, or isolated layout fragments
as requirements. Numerical package dimensions are requirements, but package names and
ordering data are not.

## Axis 2: Is this related to automotive E/E (Electrical/Electronic) systems?
E/E components include: sensors, controllers (ECU/TCU/VCU/BMS/MCU), actuators,
electric motors, power electronics (inverters, converters), power supply/wiring,
communication networks (CAN, LIN, Ethernet), diagnostics, EMC, and related interfaces.

Do not infer E/E relevance merely because the surrounding document is automotive.
Judge this record from its own text plus the supplied heading context.
Mechanical package dimensions and ordering data are not E/E. Prefer the most specific
category: CAN/LIN/bus behavior is Communication & Diagnostics; regulator/supply
behavior is Power Electronics; magnetic sensing behavior is Sensor. Use ECU & Software
only for controller/software/register logic that fits no more specific category.

Examples:
- "Type: X | Package: Y | Marking: Z" => requirement=false, E/E=false.
- "CANH" or a diagram list of signal names => requirement=false.
- "Pin TxD: low drives a dominant bus state" => requirement=true, E/E=true,
  category=Communication & Diagnostics.
- "Symbol VCC | Min 4.5 | Max 5.5 | Unit V" => requirement=true, E/E=true.
- "Package width | Min 4.8 | Max 5.0 | Unit mm" => requirement=true, E/E=false.

{heading_context}Text:
{text}

Return the requested structured fields. Keep reasoning brief and factual.
Return JSON only with exactly these fields:
{{
  "is_requirement": true,
  "is_ee_related": false,
  "ee_category": "Other",
  "reasoning": "Brief factual explanation"
}}
"""

TRANSLATION_SYSTEM_PROMPT = (
    "Translate automotive technical English to precise Korean. Return only the translation."
)

TRANSLATION_PROMPT = """Translate this automotive E/E requirement into Korean.
Preserve requirement force, identifiers, numerical values, units, and technical terminology.
Do not invent Korean meanings for technical terms. Keep English in parentheses when
ambiguity remains. Use: transceiver=트랜시버, electromagnetic emission=전자기 방출,
electromagnetic immunity=전자기 내성, EMC=전자파 적합성(EMC), decoupling
capacitor=디커플링 커패시터, supply voltage=전원 전압, bus wake-up=버스 웨이크업,
dominant/recessive=도미넌트(dominant)/리세시브(recessive), pull-up/pull-down=풀업/풀다운,
ground/GND=접지/GND, common-mode choke=공통 모드 초크.

Original: {text}
"""


# ---------------------------------------------------------------------------
# Dataclass for pipeline config
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    input_dir: Path = field(default_factory=lambda: INPUT_DIR)
    output_dir: Path = field(default_factory=lambda: OUTPUT_DIR)
    _run_dir: Path | None = field(default=None, repr=False)
    chatt_api_key: str = field(default_factory=lambda: os.getenv("CHATT_API_KEY", CHATT_API_KEY).strip())
    chatt_base_url: str = field(default_factory=lambda: os.getenv("CHATT_BASE_URL", CHATT_BASE_URL).strip())
    chatt_model: str = field(default_factory=lambda: os.getenv("CHATT_MODEL", CHATT_MODEL).strip())
    classification_system_prompt: str = CLASSIFICATION_SYSTEM_PROMPT
    classification_prompt: str = CLASSIFICATION_PROMPT
    translation_system_prompt: str = TRANSLATION_SYSTEM_PROMPT
    translation_prompt: str = TRANSLATION_PROMPT
    keywords: dict = field(default_factory=load_keywords)
    # Minimum keywords to consider a file as EE-related
    min_primary_keywords: int = 5
    # Confidence threshold for AI prescreening
    ai_confidence_threshold: float = 0.7
    request_timeout: float = 60.0
    max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("CHATT_MAX_CONCURRENCY", "4"))
    )
    max_retries: int = 3
    # Keep this at or below the limit confirmed by the ChatT administrator.
    chatt_requests_per_minute: int = field(
        default_factory=lambda: int(os.getenv("CHATT_REQUESTS_PER_MINUTE", "60"))
    )

    @property
    def run_dir(self) -> Path:
        """Per-run output subfolder: output/run_YYYYMMDD_HHMMSS/"""
        if self._run_dir is None:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._run_dir = self.output_dir / f"run_{ts}"
        return self._run_dir

    def prepare_run_dir(self) -> Path:
        """Create the output directory and return it.

        Keeping this explicit makes modules safe to run independently and avoids
        relying on the orchestrator to create the directory first.
        """
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return self.run_dir
