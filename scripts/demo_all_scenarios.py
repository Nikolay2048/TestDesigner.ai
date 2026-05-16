"""
Demo: run ALL scenarios from data/scenarios/ through Agent 1 → Agent 2.

Usage (from project root):
    1. Start mock:   python src/mock.py
    2. Run demo:     python scripts/demo_all_scenarios.py [--scenario UC-002]

Options:
    --scenario <name>   Run only the scenario whose filename contains <name>
    --skip-agent1       Load cached Agent 1 output from output/agent1_cache/
    --save-agent1       Save Agent 1 output to output/agent1_cache/ for reuse

The mock server is reset (POST /v1/debug/reset) before each scenario run.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import logging

from src.utils.config import get_config
from src.utils.logging_setup import setup_logging
from src.modules.swagger_parser import SwaggerParser
from src.agents.agent1_scenario_builder import ScenarioBuilderAgent
from src.agents.agent2_executor import ExecutorAgent
from src.models.scenario import ScenarioStabilizationInput
from src.models.execution import ScenarioExecutionResult

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

cfg = get_config()
setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)
log = logging.getLogger(__name__)

SCENARIOS_DIR  = Path("data/scenarios")
SPEC_PATH      = Path("data/openapi.yaml")
CONST_PATH     = Path(cfg.paths.constants)
CACHE_DIR      = Path("output/agent1_cache")
OUTPUT_DIR     = Path("output")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ANSI_RESET  = "\033[0m"
ANSI_GREEN  = "\033[32m"
ANSI_RED    = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_BOLD   = "\033[1m"
ANSI_CYAN   = "\033[36m"


def clr(text: str, code: str) -> str:
    return f"{code}{text}{ANSI_RESET}"


def reset_mock(base_url: str) -> bool:
    try:
        r = httpx.post(f"{base_url}/v1/debug/reset", timeout=5)
        return r.status_code == 200
    except Exception as exc:
        log.error("Failed to reset mock: %s", exc)
        return False


def load_cache(cache_path: Path) -> ScenarioStabilizationInput | None:
    if cache_path.exists():
        try:
            return ScenarioStabilizationInput.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            log.warning("Cache invalid (%s): %s", cache_path.name, exc)
    return None


def save_cache(cache_path: Path, scenario: ScenarioStabilizationInput) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        scenario.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def run_one(
    scenario_file: Path,
    spec,
    constants: dict,
    base_url: str,
    agent1: ScenarioBuilderAgent,
    use_cache: bool,
    save_to_cache: bool,
) -> tuple[ScenarioStabilizationInput, ScenarioExecutionResult, float, float]:
    """Run Agent 1 + Agent 2 for one scenario file. Returns (plan, result, t1, t2)."""

    scenario_text = scenario_file.read_text(encoding="utf-8")
    stem = scenario_file.stem
    cache_path = CACHE_DIR / f"{stem}.json"

    # --- Agent 1 ---
    t0 = time.perf_counter()
    plan = None
    if use_cache:
        plan = load_cache(cache_path)
        if plan:
            log.info("Agent1 [CACHE] %s", stem)

    if plan is None:
        log.info("Agent1 running for %s ...", stem)
        plan = agent1.build(scenario_text, spec, constants)
        if save_to_cache:
            save_cache(cache_path, plan)

    t1 = time.perf_counter() - t0

    # --- Reset mock ---
    reset_mock(base_url)

    # --- Agent 2 ---
    t0 = time.perf_counter()
    with ExecutorAgent(cfg, base_url) as executor:
        result = executor.run(plan, constants)
    t2 = time.perf_counter() - t0

    return plan, result, t1, t2


def print_scenario_result(
    scenario_file: Path,
    plan: ScenarioStabilizationInput,
    result: ScenarioExecutionResult,
    t1: float,
    t2: float,
) -> None:
    overall_ok = result.overall_status == "passed"
    status_str = clr("✔ PASSED", ANSI_GREEN) if overall_ok else clr("✘ FAILED", ANSI_RED)

    print(f"\n{'─' * 70}")
    print(clr(f"  {scenario_file.stem}", ANSI_BOLD))
    print(f"  Scenario : {result.scenario_name}")
    print(f"  Result   : {status_str}   "
          f"({result.passed_steps}/{result.total_steps} steps passed)")
    print(f"  Timing   : Agent1={t1:.1f}s  Agent2={t2:.1f}s")

    for step in result.steps:
        if step.status == "passed":
            icon = clr("✔", ANSI_GREEN)
        elif step.status == "failed":
            icon = clr("✘", ANSI_RED)
        else:
            icon = clr("·", ANSI_YELLOW)

        url_short = step.url.replace("http://localhost:8080", "")
        status_part = f"  HTTP {step.response_status}" if step.response_status else ""
        print(f"  {icon} Step {step.step_num}: {step.name:<42}{status_part}")

        if step.status == "failed" and step.error:
            short_err = step.error[:90] + ("…" if len(step.error) > 90 else "")
            print(f"      {clr('⚠ ' + short_err, ANSI_RED)}")

        for ar in step.assertion_results:
            if not ar.passed:
                print(f"      {clr('✘ [' + ar.operator + '] ' + ar.path, ANSI_RED)}"
                      f"  expected={ar.expected!r}  actual={ar.actual!r}")


def print_summary_table(
    rows: list[tuple[Path, ScenarioExecutionResult, float, float]],
) -> None:
    print(f"\n{'=' * 70}")
    print(clr("  SUMMARY", ANSI_BOLD))
    print(f"{'=' * 70}")
    print(f"  {'Scenario':<42} {'Result':<10} {'Steps':>6}  {'t1':>5}  {'t2':>5}")
    print(f"  {'─' * 42} {'─' * 10} {'─' * 6}  {'─' * 5}  {'─' * 5}")

    total_passed = 0
    total_failed = 0
    for scenario_file, result, t1, t2 in rows:
        ok = result.overall_status == "passed"
        status = clr("PASSED", ANSI_GREEN) if ok else clr("FAILED", ANSI_RED)
        steps = f"{result.passed_steps}/{result.total_steps}"
        name = scenario_file.stem[:42]
        print(f"  {name:<42} {status:<10} {steps:>6}  {t1:>4.0f}s  {t2:>4.0f}s")
        if ok:
            total_passed += 1
        else:
            total_failed += 1

    print(f"{'─' * 70}")
    total = total_passed + total_failed
    pct = int(total_passed / total * 100) if total else 0
    overall = clr(f"✔ ALL PASSED ({total})", ANSI_GREEN) if total_failed == 0 \
        else clr(f"✘ {total_failed}/{total} FAILED", ANSI_RED)
    print(f"  {overall}   ({pct}% pass rate)")
    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run all carsharing test scenarios")
    parser.add_argument("--scenario", default="", help="Filter: only run files whose name contains this string")
    parser.add_argument("--skip-agent1", action="store_true", help="Load cached Agent 1 output")
    parser.add_argument("--save-agent1", action="store_true", help="Save Agent 1 output to cache")
    args = parser.parse_args()

    # Validate paths
    for p in (SCENARIOS_DIR, SPEC_PATH, CONST_PATH):
        if not p.exists():
            log.error("Required path not found: %s", p.resolve())
            sys.exit(1)

    constants: dict = json.loads(CONST_PATH.read_text(encoding="utf-8"))
    base_url = constants.get("base_url", "http://localhost:8080")

    # Check mock is up
    try:
        httpx.get(f"{base_url}/health", timeout=3).raise_for_status()
        log.info("Mock server is UP at %s", base_url)
    except Exception:
        log.error("Mock server not reachable at %s — start it first: python src/mock.py", base_url)
        sys.exit(1)

    # Parse spec (once)
    log.info("Parsing OpenAPI spec...")
    spec = SwaggerParser(SPEC_PATH).parse()

    # Collect scenario files
    scenario_files = sorted(SCENARIOS_DIR.glob("*.md"))
    if args.scenario:
        scenario_files = [f for f in scenario_files if args.scenario.lower() in f.stem.lower()]

    if not scenario_files:
        log.error("No scenario files found in %s (filter: '%s')", SCENARIOS_DIR, args.scenario)
        sys.exit(1)

    log.info("Found %d scenario(s) to run", len(scenario_files))

    # Create Agent 1 (shared across all scenarios)
    agent1 = ScenarioBuilderAgent(cfg)

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Run scenarios
    rows: list[tuple[Path, ScenarioExecutionResult, float, float]] = []

    for i, scenario_file in enumerate(scenario_files, 1):
        print(f"\n{'═' * 70}")
        print(clr(f"  [{i}/{len(scenario_files)}] {scenario_file.stem}", ANSI_CYAN + ANSI_BOLD))
        print(f"{'═' * 70}")

        try:
            plan, result, t1, t2 = run_one(
                scenario_file=scenario_file,
                spec=spec,
                constants=constants,
                base_url=base_url,
                agent1=agent1,
                use_cache=args.skip_agent1,
                save_to_cache=args.save_agent1,
            )

            # Save detailed output
            out_file = OUTPUT_DIR / f"result_{scenario_file.stem}.json"
            out_file.write_text(
                result.model_dump_json(indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            print_scenario_result(scenario_file, plan, result, t1, t2)
            rows.append((scenario_file, result, t1, t2))

        except Exception as exc:
            log.exception("Error running scenario %s: %s", scenario_file.stem, exc)
            print(clr(f"  ⚠ EXCEPTION: {exc}", ANSI_RED))

    print_summary_table(rows)


if __name__ == "__main__":
    main()
