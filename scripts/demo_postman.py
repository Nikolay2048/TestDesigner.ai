"""
Demo: generate a Postman collection from all cached Agent 1 outputs.

Usage (from project root):
    python scripts/demo_postman.py

Reads scenario plans from output/agent1_cache/*.json (produced by
demo_all_scenarios.py --save-agent1) and writes the collection to
output/postman_collection.json.

If no cache is found for a scenario, Agent 1 is run on-the-fly.
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
from src.models.scenario import ScenarioStabilizationInput
from src.modules.swagger_parser import SwaggerParser
from src.modules.postman_generator import PostmanGenerator

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

cfg = get_config()
setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
log = logging.getLogger(__name__)

SCENARIOS_DIR = Path("data/scenarios")
SPEC_PATH     = Path("data/openapi.yaml")
CONST_PATH    = Path(cfg.paths.constants)
CACHE_DIR     = Path("output/agent1_cache")
OUTPUT_PATH   = Path("output/postman_collection.json")

# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------

constants: dict = json.loads(CONST_PATH.read_text(encoding="utf-8"))
scenario_files = sorted(SCENARIOS_DIR.glob("*.md"))

if not scenario_files:
    log.error("No scenario files found in %s", SCENARIOS_DIR)
    sys.exit(1)

log.info("Loading %d scenario plan(s)...", len(scenario_files))

plans: list[ScenarioStabilizationInput] = []
missing_cache: list[Path] = []

for sf in scenario_files:
    cache_path = CACHE_DIR / f"{sf.stem}.json"
    if cache_path.exists():
        try:
            plan = ScenarioStabilizationInput.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
            plans.append(plan)
            log.info("  [cache] %s → %d steps", sf.stem, len(plan.steps))
            continue
        except Exception as exc:
            log.warning("  Cache invalid for %s: %s", sf.stem, exc)
    missing_cache.append(sf)

# Run Agent 1 for scenarios without cache
if missing_cache:
    from src.agents.agent1_scenario_builder import ScenarioBuilderAgent
    spec = SwaggerParser(SPEC_PATH).parse()
    agent1 = ScenarioBuilderAgent(cfg)

    for sf in missing_cache:
        log.info("  [Agent1] running for %s ...", sf.stem)
        scenario_text = sf.read_text(encoding="utf-8")
        plan = agent1.build(scenario_text, spec, constants)

        # Save to cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / f"{sf.stem}.json"
        cache_path.write_text(
            plan.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8"
        )
        plans.append(plan)
        log.info("    → %d steps (cached)", len(plan.steps))

# Preserve scenario file order
stem_to_plan = {p.scenario_name: p for p in plans}
# Re-sort by filename order
plans_ordered: list[ScenarioStabilizationInput] = []
for sf in scenario_files:
    cache_path = CACHE_DIR / f"{sf.stem}.json"
    if cache_path.exists():
        p = ScenarioStabilizationInput.model_validate_json(
            cache_path.read_text(encoding="utf-8")
        )
        plans_ordered.append(p)

# ---------------------------------------------------------------------------
# Generate collection
# ---------------------------------------------------------------------------

log.info("Generating Postman collection...")
generator = PostmanGenerator(reset_mock_between_scenarios=True)
collection = generator.generate(
    scenarios=plans_ordered,
    constants=constants,
    collection_name="Carsharing API — Test Suite",
)

# ---------------------------------------------------------------------------
# Save output
# ---------------------------------------------------------------------------

OUTPUT_PATH.parent.mkdir(exist_ok=True)
OUTPUT_PATH.write_text(
    json.dumps(collection, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

total_folders = len(collection["item"])
total_requests = sum(len(f["item"]) for f in collection["item"])
total_vars = len(collection["variable"])

print(f"\n{'=' * 60}")
print(f"  Postman Collection generated")
print(f"{'=' * 60}")
print(f"  Output     : {OUTPUT_PATH.resolve()}")
print(f"  Folders    : {total_folders}")
print(f"  Requests   : {total_requests}  (incl. reset helpers)")
print(f"  Variables  : {total_vars}")
print(f"{'─' * 60}")

for folder in collection["item"]:
    items = folder["item"]
    step_items = [i for i in items if not i["name"].startswith("⚙")]
    print(f"  📁 {folder['name']}")
    for item in items:
        prefix = "    ⚙" if item["name"].startswith("⚙") else "    •"
        print(f"  {prefix} {item['name']}")

print(f"\n{'=' * 60}")
print(f"  Variables defined in collection:")
print(f"{'─' * 60}")
for var in collection["variable"]:
    val = var["value"]
    display = val if val else "(empty — set at runtime)"
    print(f"  {{{{ {var['key']} }}}} = {display}")

print(f"\n{'=' * 60}")
print("  Import into Postman:")
print("  File → Import → select output/postman_collection.json")
print(f"{'=' * 60}\n")
