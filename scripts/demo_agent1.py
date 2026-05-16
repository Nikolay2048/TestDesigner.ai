"""
Demo script: run Agent 1 on the carsharing scenario and print the result.

Usage (from project root):
    python scripts/demo_agent1.py

The script reads:
    data/scenario.md    — business scenario
    data/openapi.yaml   — OpenAPI spec (multi-file, auto-resolved)
    data/constants.json — runtime constants (userId, city, …)
    config.yaml         — LLM provider settings

Output: ScenarioStabilizationInput as pretty-printed JSON.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

# Force UTF-8 on Windows (Cyrillic safe)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

from src.utils.config import get_config
from src.utils.logging_setup import setup_logging
from src.modules.swagger_parser import SwaggerParser
from src.agents.agent1_scenario_builder import ScenarioBuilderAgent

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

cfg = get_config()
setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load inputs
# ---------------------------------------------------------------------------

SCENARIO_PATH = Path("data/scenario.md")
SPEC_PATH = Path("data/openapi.yaml")
CONSTANTS_PATH = Path(cfg.paths.constants)

for p in (SCENARIO_PATH, SPEC_PATH, CONSTANTS_PATH):
    if not p.exists():
        log.error("Required file not found: %s", p.resolve())
        sys.exit(1)

scenario_text = SCENARIO_PATH.read_text(encoding="utf-8")
constants: dict = json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))

log.info("Parsing OpenAPI spec: %s", SPEC_PATH)
spec = SwaggerParser(SPEC_PATH).parse()
log.info("Spec parsed: %d endpoint(s)", len(spec.endpoints))

# ---------------------------------------------------------------------------
# Run Agent 1
# ---------------------------------------------------------------------------

log.info("Running Agent 1 (ScenarioBuilderAgent)...")
agent = ScenarioBuilderAgent(cfg)
result = agent.build(scenario_text, spec, constants)

# ---------------------------------------------------------------------------
# Print result
# ---------------------------------------------------------------------------

print("\n" + "=" * 70)
print(f"  Scenario : {result.scenario_name}")
print(f"  Desc     : {result.description}")
print(f"  Steps    : {len(result.steps)}")
print("=" * 70)

for step in result.steps:
    print(f"\nStep {step.step_num}: {step.name}")
    print(f"  {step.method} {step.path}")
    if step.path_params:
        print(f"  path_params  : {step.path_params}")
    if step.query_params:
        print(f"  query_params : {step.query_params}")
    if step.body:
        print(f"  body         : {json.dumps(step.body, ensure_ascii=False)}")
    print(f"  expect_status: {step.expected_status_code}")
    if step.extract_vars:
        print(f"  extract_vars : {step.extract_vars}")
    if step.assertions:
        print(f"  assertions   ({len(step.assertions)}):")
        for a in step.assertions:
            exp = f" == {json.dumps(a.expected)}" if a.expected is not None else ""
            print(f"    [{a.operator}] {a.path}{exp}  # {a.description}")

print("\n" + "=" * 70)
print("Full JSON output:")
print("=" * 70)
print(result.model_dump_json(indent=2, ensure_ascii=False))
