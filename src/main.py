from __future__ import annotations

import argparse
from pathlib import Path

from llm import NoLLM, OllamaLLM
from orchestrator import AgenticTestDesignOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TestDesignerAI agentic learning scaffold")
    parser.add_argument("--scenario", required=True, help="Path to a scenario .md file")
    parser.add_argument("--out", default="runs/latest", help="Directory for agent prompts and state")
    parser.add_argument("--llm", choices=["none", "ollama"], default="ollama")
    parser.add_argument("--model", default="qwen3:14b", help="Ollama model name")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    llm = NoLLM() if args.llm == "none" else OllamaLLM(model=args.model, base_url=args.ollama_url)

    orchestrator = AgenticTestDesignOrchestrator(llm=llm)
    state = orchestrator.run(args.scenario, args.out)

    last_run = state.agent_runs[-1] if state.agent_runs else None
    print("TestDesignerAI")
    print("=" * 60)
    print(f"Scenario:   {state.scenario.title}")
    print(f"Artifacts:  {Path(args.out).resolve()}")
    if state.understanding:
        print(f"Steps:      {len(state.understanding.business_steps)}")
        print(f"Endpoints:  {len(state.understanding.endpoint_mentions)}")
    if last_run:
        print(f"Last agent: {last_run.agent_name} -> {last_run.status}")
        if last_run.status == "needs_llm":
            print("Next step: inspect the generated prompt, then run with --llm ollama or improve the agent contract.")


if __name__ == "__main__":
    main()
