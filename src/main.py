from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from llm import NoLLM, OllamaLLM
from orchestrator import AgenticTestDesignOrchestrator
from scenario_dependencies import ScenarioDependencyRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TestDesignerAI agentic learning scaffold")
    parser.add_argument("--scenario", required=True, help="Path to a scenario .md file")
    parser.add_argument("--openapi", required=True, help="Path to openapi.yaml")
    parser.add_argument("--out", default="runs/latest", help="Directory for agent prompts and state")
    parser.add_argument("--test-data", default=None, help="Optional JSON/YAML file with tester-provided constants")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL for happy-path execution")
    parser.add_argument("--max-attempts", type=int, default=7, help="Maximum stabilization attempts")
    parser.add_argument("--stable-dir", default="runs/stable", help="Directory with published stable scenarios")
    parser.add_argument(
        "--resolve-dependencies",
        action="store_true",
        help="Run only with already-published stable scenario dependencies",
    )
    parser.add_argument("--llm", choices=["none", "ollama"], default="ollama")
    parser.add_argument("--model", default="qwen3:14b", help="Ollama model name")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument(
        "--run-test-cases",
        action="store_true",
        help="Execute generated test cases after happy-path stabilization and save their traces",
    )
    parser.add_argument(
        "--export-postman",
        action="store_true",
        help="Export Postman collections and environment after successful test design",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    llm = NoLLM() if args.llm == "none" else OllamaLLM(model=args.model, base_url=args.ollama_url)

    if args.resolve_dependencies:
        runner = ScenarioDependencyRunner(llm=llm)
        state = runner.run(
            args.scenario,
            args.openapi,
            args.out,
            args.test_data,
            base_url=args.base_url,
            max_attempts=args.max_attempts,
            stable_dir=args.stable_dir,
            run_test_cases=args.run_test_cases,
            export_postman=args.export_postman,
        )
    else:
        orchestrator = AgenticTestDesignOrchestrator(llm=llm)
        state = orchestrator.run(
            args.scenario,
            args.openapi,
            args.out,
            args.test_data,
            base_url=args.base_url,
            max_attempts=args.max_attempts,
            stable_dir=args.stable_dir,
            publish_stable=True,
            run_test_cases=args.run_test_cases,
            export_postman=args.export_postman,
        )

    last_run = state.agent_runs[-1] if state.agent_runs else None
    print("TestDesignerAI")
    print("=" * 60)
    print(f"Scenario:   {state.scenario.title}")
    print(f"Operations: {len(state.operations)}")
    print(f"Artifacts:  {Path(args.out).resolve()}")
    print(f"Stable dir: {Path(args.stable_dir).resolve()}")
    if state.understanding:
        print(f"Steps:      {len(state.understanding.business_steps)}")
        print(f"Endpoints:  {len(state.understanding.endpoint_mentions)}")
    if state.endpoint_mapping:
        print(f"Mappings:   {len(state.endpoint_mapping.mappings)}")
    if state.data_dependency_graph:
        print(f"Dep steps:  {len(state.data_dependency_graph.steps)}")
    if state.data_binding:
        print(f"Bindings:   {len(state.data_binding.steps)}")
    if state.stabilization:
        print(f"Happy path: {state.stabilization.status}")
        print(f"Attempts:   {len(state.stabilization.attempts)}")
    if state.test_design:
        print(f"Test cases: {len(state.test_design.test_cases)}")
        if state.test_design.executions:
            print(f"TC runs:    {len(state.test_design.executions)}")
            print(f"TC statuses:{_format_execution_statuses(state.test_design.executions)}")
        if args.export_postman:
            print(f"Postman:    {Path(args.out).resolve() / 'postman'}")
    if last_run:
        print(f"Last agent: {last_run.agent_name} -> {last_run.status}")
        if last_run.status == "needs_llm":
            print("Next step: inspect the generated prompt, then run with --llm ollama or improve the agent contract.")


def _format_execution_statuses(executions) -> str:
    counts = Counter(item.status for item in executions)
    parts = [
        f"{status}={counts[status]}"
        for status in [
            "passed",
            "failed",
            "review_required",
            "blocked",
            "contract_mismatch",
            "oracle_incomplete",
            "weak_attack",
            "not_run",
        ]
        if counts[status]
    ]
    return " " + ", ".join(parts) if parts else " none"


if __name__ == "__main__":
    main()
