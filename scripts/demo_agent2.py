"""
Demo script: run the full pipeline — Agent 1 → Agent 2 — against the mock server.

Usage (from project root):
    1. Start the mock server:   python run_mock_server.py
    2. Run this script:         python scripts/demo_agent2.py

What it does:
    1. Parses the OpenAPI spec.
    2. Runs Agent 1 to build the test plan (ScenarioStabilizationInput).
    3. Runs Agent 2 to execute every step against http://localhost:8080.
    4. Prints a coloured pass/fail report.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

from src.utils.config import get_config
from src.utils.logging_setup import setup_logging
from src.modules.swagger_parser import SwaggerParser
from src.agents.agent1_scenario_builder import ScenarioBuilderAgent
from src.agents.agent2_executor import ExecutorAgent

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

cfg = get_config()
setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
log = logging.getLogger(__name__)

SCENARIO_PATH = Path("data/scenario.md")
SPEC_PATH     = Path("data/openapi.yaml")
CONST_PATH    = Path(cfg.paths.constants)

for p in (SCENARIO_PATH, SPEC_PATH, CONST_PATH):
    if not p.exists():
        log.error("Required file not found: %s", p.resolve())
        sys.exit(1)

constants: dict = json.loads(CONST_PATH.read_text(encoding="utf-8"))
scenario_text   = SCENARIO_PATH.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Step 1 — parse spec
# ---------------------------------------------------------------------------

log.info("Parsing OpenAPI spec...")
spec = SwaggerParser(SPEC_PATH).parse()

# ---------------------------------------------------------------------------
# Step 2 — Agent 1: build test plan
# ---------------------------------------------------------------------------

log.info("Running Agent 1 (ScenarioBuilderAgent)...")
scenario = ScenarioBuilderAgent(cfg).build(scenario_text, spec, constants)

log.info("Agent 1 produced %d steps", len(scenario.steps))

# ---------------------------------------------------------------------------
# Step 3 — Agent 2: execute
# ---------------------------------------------------------------------------

base_url = constants.get("base_url", spec.base_url)
log.info("Running Agent 2 against %s ...", base_url)

with ExecutorAgent(cfg, base_url) as executor:
    result = executor.run(scenario, constants)

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

STATUS_ICON = {"passed": PASS, "failed": FAIL, "skipped": SKIP}

print("\n" + "=" * 70)
print(f"  Scenario : {result.scenario_name}")
overall_icon = "\033[32m✔ PASSED\033[0m" if result.overall_status == "passed" else "\033[31m✘ FAILED\033[0m"
print(f"  Result   : {overall_icon}")
print(f"  Steps    : {result.passed_steps}/{result.total_steps} passed")
print("=" * 70)

for step in result.steps:
    icon = STATUS_ICON.get(step.status, step.status)
    print(f"\n[{icon}] Step {step.step_num}: {step.name}")

    if step.status == "skipped":
        print("       (skipped — previous step failed)")
        continue

    print(f"       {step.method} {step.url}")
    if step.response_status is not None:
        print(f"       HTTP {step.response_status}  (attempts: {step.attempts})")

    if step.extracted_vars:
        for k, v in step.extracted_vars.items():
            print(f"       ↳ extracted  {k} = {v!r}")

    if step.assertion_results:
        for ar in step.assertion_results:
            mark = "✔" if ar.passed else "✘"
            actual_str = f"  actual={ar.actual!r}" if ar.actual is not None else ""
            print(f"       {mark} [{ar.operator}] {ar.path}{actual_str}  # {ar.description}")

    if step.error:
        print(f"       ⚠ ERROR: {step.error}")

print("\n" + "=" * 70)
print("Full JSON result saved to: output_agent2.json")
print("=" * 70)

out_path = Path("output_agent2.json")
out_path.write_text(
    result.model_dump_json(indent=2, ensure_ascii=False),
    encoding="utf-8",
)
