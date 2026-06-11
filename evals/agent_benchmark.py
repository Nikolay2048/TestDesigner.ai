from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from old.domain import (
    DependencyResolverResult,
    EndpointMappingResult,
    GenerationBindingResult,
    ScenarioUnderstanding,
    StabilizationDiagnosis,
    StabilizationFix,
)
from old.llm import extract_json


DEFAULT_MODELS = [
    "qwen3:14b",
    "qwen3.5:27b",
    "qwen3.5:35b",
    "qwen3:30b-a3b",
]


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    agent_name: str
    run_path: str
    output_model: type[BaseModel]
    expected: dict[str, Any] = field(default_factory=dict)


CASES = [
    BenchmarkCase(
        case_id="documentation_carsharing",
        agent_name="Documentation Analyst",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "documentation_analyst.run.json"
        ),
        output_model=ScenarioUnderstanding,
        expected={"min_business_steps": 6},
    ),
    BenchmarkCase(
        case_id="documentation_clinic",
        agent_name="Documentation Analyst",
        run_path=(
            "runs/clinic_20260607_215930/01-main-happy-path/"
            "documentation_analyst.run.json"
        ),
        output_model=ScenarioUnderstanding,
        expected={"min_business_steps": 8},
    ),
    BenchmarkCase(
        case_id="endpoint_create_reservation",
        agent_name="Endpoint Mapper",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "endpoint_mapper_tasks/task_04.run.json"
        ),
        output_model=EndpointMappingResult,
        expected={"operation": ["POST", "/reservations"]},
    ),
    BenchmarkCase(
        case_id="endpoint_payment",
        agent_name="Endpoint Mapper",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "endpoint_mapper_tasks/task_05.run.json"
        ),
        output_model=EndpointMappingResult,
        expected={"operation": ["POST", "/payments/preauth"]},
    ),
    BenchmarkCase(
        case_id="endpoint_outcome_is_not_duplicate_call",
        agent_name="Endpoint Mapper",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "endpoint_mapper_tasks/task_08.run.json"
        ),
        output_model=EndpointMappingResult,
        expected={"unmapped": True},
    ),
    BenchmarkCase(
        case_id="dependency_vehicle_id",
        agent_name="Dependency Resolver",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "dependency_resolver_tasks/s03_path_vehicleId.run.json"
        ),
        output_model=DependencyResolverResult,
        expected={"selected_candidate_id": "c_s02_vehicles_id"},
    ),
    BenchmarkCase(
        case_id="generation_return_date",
        agent_name="Generation Binding",
        run_path=(
            "runs/carsharing_20260610_215048/01-basic-economy-rental/"
            "generation_binding_tasks/s02_returnDate.run.json"
        ),
        output_model=GenerationBindingResult,
        expected={"source": "generated", "generator": "date_after_now"},
    ),
    BenchmarkCase(
        case_id="diagnosis_invalid_dates",
        agent_name="Stabilization Diagnostician",
        run_path=(
            "runs/carsharing/01-basic-economy-rental/"
            "stabilization_diagnosis/attempt_01.run.json"
        ),
        output_model=StabilizationDiagnosis,
        expected={"failed_step_id": "s02", "target": "$.returnDate"},
    ),
    BenchmarkCase(
        case_id="fix_invalid_dates",
        agent_name="Stabilization Fixer",
        run_path=(
            "runs/carsharing/01-basic-economy-rental/"
            "stabilization_fixes/attempt_01_try_01.run.json"
        ),
        output_model=StabilizationFix,
        expected={
            "patch_type": "replace_generated_params",
            "target": "$.returnDate",
        },
    ),
    BenchmarkCase(
        case_id="diagnosis_fixed_numeric_value",
        agent_name="Stabilization Diagnostician",
        run_path=(
            "runs/carsharing/01-basic-economy-rental/"
            "stabilization_diagnosis/attempt_02.run.json"
        ),
        output_model=StabilizationDiagnosis,
        expected={"failed_step_id": "s06", "target": "$.fuelLevelPercent"},
    ),
    BenchmarkCase(
        case_id="fix_fixed_numeric_value",
        agent_name="Stabilization Fixer",
        run_path=(
            "runs/carsharing/01-basic-economy-rental/"
            "stabilization_fixes/attempt_02_try_01.run.json"
        ),
        output_model=StabilizationFix,
        expected={
            "patch_type": "replace_generated_params",
            "target": "$.fuelLevelPercent",
        },
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay real agent prompts against Ollama models.")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.out)
        if args.out
        else PROJECT_ROOT / "evals" / "results" / f"agent_benchmark_{timestamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = [_load_case(case) for case in CASES]
    results: list[dict[str, Any]] = []
    for model in args.models:
        _unload_models(args.ollama_url, args.timeout)
        for repetition in range(1, args.repetitions + 1):
            for case, messages in cases:
                print(f"[{model}] repeat={repetition} {case.case_id}", flush=True)
                result = _run_case(
                    model=model,
                    case=case,
                    messages=messages,
                    base_url=args.ollama_url,
                    num_ctx=args.num_ctx,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    seed=41 + repetition,
                )
                result["repetition"] = repetition
                results.append(result)
                model_dir = output_dir / _safe_name(model)
                model_dir.mkdir(parents=True, exist_ok=True)
                (model_dir / f"{case.case_id}_repeat_{repetition:02d}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    summary = _build_summary(results, args)
    (output_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown_summary(output_dir / "summary.md", summary)
    print(json.dumps(summary["models"], ensure_ascii=False, indent=2))
    print(f"Saved to {output_dir}")


def _load_case(case: BenchmarkCase) -> tuple[BenchmarkCase, list[dict[str, str]]]:
    path = PROJECT_ROOT / case.run_path
    data = json.loads(path.read_text(encoding="utf-8"))
    messages = [
        {"role": item["role"], "content": item["content"]}
        for item in data.get("prompt", [])
    ]
    if not messages:
        raise ValueError(f"Benchmark fixture has no prompt: {path}")
    return case, messages


def _run_case(
    model: str,
    case: BenchmarkCase,
    messages: list[dict[str, str]],
    base_url: str,
    num_ctx: int,
    temperature: float,
    timeout: float,
    seed: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
        "think": False,
        "keep_alive": "5m",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "seed": seed,
        },
    }
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        raw = data["message"]["content"]
        error = None
    except Exception as exc:
        data = {}
        raw = ""
        error = str(exc)
    wall_seconds = time.perf_counter() - started

    parsed = None
    validated = None
    json_valid = False
    schema_valid = False
    semantic_checks: list[dict[str, Any]] = []
    if not error:
        try:
            parsed = extract_json(raw)
            json_valid = True
            validated = case.output_model.model_validate(parsed)
            schema_valid = True
            semantic_checks = _semantic_checks(case, validated)
        except (ValueError, ValidationError) as exc:
            error = str(exc)

    semantic_valid = bool(semantic_checks) and all(item["passed"] for item in semantic_checks)
    eval_count = int(data.get("eval_count") or 0)
    eval_seconds = float(data.get("eval_duration") or 0) / 1e9
    return {
        "model": model,
        "case_id": case.case_id,
        "agent_name": case.agent_name,
        "fixture": case.run_path,
        "json_valid": json_valid,
        "schema_valid": schema_valid,
        "semantic_valid": semantic_valid,
        "passed": schema_valid and semantic_valid,
        "semantic_checks": semantic_checks,
        "wall_seconds": round(wall_seconds, 3),
        "load_seconds": round(float(data.get("load_duration") or 0) / 1e9, 3),
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "output_tokens": eval_count,
        "tokens_per_second": round(eval_count / eval_seconds, 3) if eval_seconds else 0.0,
        "error": error,
        "seed": seed,
        "raw_output": raw,
        "parsed_output": parsed,
    }


def _semantic_checks(case: BenchmarkCase, output: BaseModel) -> list[dict[str, Any]]:
    expected = case.expected
    checks: list[tuple[str, bool, Any]] = []
    if case.agent_name == "Documentation Analyst":
        steps = output.business_steps
        checks.append(("minimum business step count", len(steps) >= expected["min_business_steps"], len(steps)))
    elif case.agent_name == "Endpoint Mapper":
        operations = {
            (operation.method.upper(), operation.path)
            for mapping in output.mappings
            for operation in mapping.operations
        }
        if expected.get("unmapped"):
            checks.append(
                (
                    "outcome step is not mapped to a duplicate REST call",
                    not operations and bool(output.unmapped_steps),
                    {
                        "operations": sorted(operations),
                        "unmapped_steps": len(output.unmapped_steps),
                    },
                )
            )
        else:
            operation = tuple(expected["operation"])
            checks.append(("expected OpenAPI operation selected", operation in operations, sorted(operations)))
    elif case.agent_name == "Dependency Resolver":
        selected = [
            item.selected_candidate_id
            for item in output.resolutions
            if item.selected_candidate_id
        ]
        checks.append(
            (
                "expected producer selected",
                expected["selected_candidate_id"] in selected,
                selected,
            )
        )
    elif case.agent_name == "Generation Binding":
        decisions = output.decisions
        matching = [
            item
            for item in decisions
            if item.source == expected["source"] and item.generator == expected["generator"]
        ]
        checks.append(("expected generator policy selected", bool(matching), [item.model_dump() for item in decisions]))
    elif case.agent_name == "Stabilization Diagnostician":
        targets = [item.get("target") for item in output.suspected_bindings]
        checks.append(("failed step identified", output.failed_step_id == expected["failed_step_id"], output.failed_step_id))
        checks.append(("faulty binding identified", expected["target"] in targets, targets))
    elif case.agent_name == "Stabilization Fixer":
        patches = output.patches
        checks.append(
            (
                "safe patch type selected",
                any(item.patch_type == expected["patch_type"] for item in patches),
                [item.patch_type for item in patches],
            )
        )
        checks.append(
            (
                "correct binding target selected",
                any(item.target == expected["target"] for item in patches),
                [item.target for item in patches],
            )
        )
    return [
        {"name": name, "passed": passed, "actual": actual}
        for name, passed, actual in checks
    ]


def _build_summary(results: list[dict[str, Any]], args) -> dict[str, Any]:
    models = {}
    for model in args.models:
        rows = [item for item in results if item["model"] == model]
        models[model] = {
            "cases": len(rows),
            "passed": sum(bool(item["passed"]) for item in rows),
            "json_valid": sum(bool(item["json_valid"]) for item in rows),
            "schema_valid": sum(bool(item["schema_valid"]) for item in rows),
            "semantic_valid": sum(bool(item["semantic_valid"]) for item in rows),
            "total_wall_seconds": round(sum(item["wall_seconds"] for item in rows), 3),
            "average_tokens_per_second": round(
                sum(item["tokens_per_second"] for item in rows) / len(rows),
                3,
            ),
        }
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "configuration": {
            "temperature": args.temperature,
            "num_ctx": args.num_ctx,
            "think": False,
            "seeds": [42 + index for index in range(args.repetitions)],
            "repetitions": args.repetitions,
        },
        "models": models,
    }


def _write_markdown_summary(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Agent benchmark",
        "",
        "| Model | Passed | JSON | Schema | Semantic | Time, s | tok/s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model, item in summary["models"].items():
        lines.append(
            f"| {model} | {item['passed']}/{item['cases']} | "
            f"{item['json_valid']}/{item['cases']} | "
            f"{item['schema_valid']}/{item['cases']} | "
            f"{item['semantic_valid']}/{item['cases']} | "
            f"{item['total_wall_seconds']} | {item['average_tokens_per_second']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _unload_models(base_url: str, timeout: float) -> None:
    try:
        loaded = httpx.get(f"{base_url.rstrip('/')}/api/ps", timeout=10).json().get("models", [])
    except Exception:
        return
    for item in loaded:
        name = item.get("name")
        if not name:
            continue
        try:
            httpx.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={"model": name, "keep_alive": 0},
                timeout=timeout,
            )
        except Exception:
            pass


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_")


if __name__ == "__main__":
    main()
