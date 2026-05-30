"""Прогон системы тест-дизайна на предметной области «Доска объявлений».

Перед запуском: python run_mock_bulletin.py  (поднимает mock на порту 8001)

Группы:
  UC_101        — регистрация, логин, создание и публикация объявления
  UC_102        — поиск и просмотр (без авторизации)
  UC_103        — отклики: покупатель → продавец просматривает → помечает прочитанным
  UC_101+102+103 — полный связный сценарий
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.artifacts import (
    export_combined_coverage,
    export_coverage_report,
    export_stabilization_trace,
    export_test_cases_full,
)
from src.collection_builder import export_collection
from src.config import CONFIG, load_domain_config
from src.graph import build_graph

# ── Конфигурация: читается из constants.json ──────────────────────────────────
domain_cfg = load_domain_config("bulletin_board")

_all_scenarios: dict = domain_cfg.get("scenarios", {})
SCENARIO_GROUPS = [
    {
        "name": g["name"],
        "collection_name": g["collection_name"],
        "files": [
            f
            for uc in g.get("ucs", [])
            for f in _all_scenarios.get(uc, {}).get("files", [])
        ],
        "mock_supported": all(
            _all_scenarios.get(uc, {}).get("mock_supported", False)
            for uc in g.get("ucs", [])
        ),
    }
    for g in domain_cfg.get("groups", [])
]


def _export_md(test_cases: list[dict], path: Path) -> None:
    by_tech: dict[str, list[dict]] = {}
    for tc in test_cases:
        tech = tc.get("technique", "unknown")
        by_tech.setdefault(tech, []).append(tc)

    TECH_ORDER = ["happy_path", "boundary", "negative", "equivalence", "state_based"]
    lines = ["# Test Cases — Bulletin Board\n"]
    for tech in TECH_ORDER + [t for t in by_tech if t not in TECH_ORDER]:
        cases = by_tech.get(tech, [])
        if not cases:
            continue
        lines.append(f"\n## {tech.replace('_', ' ').title()} ({len(cases)})\n")
        for tc in cases:
            exp = tc.get("expected_status", "?")
            step = tc.get("target_step", "?")
            setup_n = len(tc.get("setup_chain", []))
            assertions = tc.get("assertions", [])
            lines.append(f"- **{tc['title']}**")
            lines.append(f"  - target: `{step}` | expected HTTP: `{exp}` | setup steps: {setup_n}")
            if assertions:
                lines.append(f"  - assertions: {assertions}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _print_summary(group_name: str, result: dict) -> None:
    metrics = result.get("metrics", {})
    tc_count = len(result.get("test_cases", []))
    er = result.get("exec_results", [])
    passed = sum(1 for e in er if e.get("passed"))

    print(f"\n--- {group_name} ---")
    stab = metrics.get("stabilization", {})
    stab_ok = stab.get("is_stabilized", False)
    print(f"  Stabilization: {'OK' if stab_ok else 'FAILED'} ({stab.get('total_fixes_applied', 0)} fixes)")
    print(f"  Test cases:    {tc_count}")
    if er:
        print(f"  Exec results:  {passed}/{len(er)} passed ({100*passed//len(er)}%)")
    else:
        print(f"  Exec results:  not run")

    failed = [e for e in er if not e.get("passed")]
    for f in failed:
        print(f"    FAIL [{f.get('technique','?')}] {f.get('title','?')} "
              f"actual={f.get('actual_status')} exp={f.get('expected_status')}")


def main():
    out_dir = ROOT / "output" / "bulletin_board"
    out_dir.mkdir(parents=True, exist_ok=True)

    graph = build_graph()
    all_results: list[dict] = []
    all_collections: list[dict] = []
    all_test_cases: list[dict] = []

    for group in SCENARIO_GROUPS:
        group_name = group["name"]
        print(f"\n{'='*60}")
        print(f"RUNNING: {group_name}")
        print('='*60)

        files = group["files"]
        spec_paths = [str(ROOT / f"data/scenarios/openapi_spec/{f}.yaml") for f in files]
        raw_scenarios = "\n\n".join(
            (ROOT / f"data/scenarios/{f}.md").read_text(encoding="utf-8")
            for f in files
        )

        CONFIG.collection_name = group["collection_name"]

        result = graph.invoke({
            "spec_paths": spec_paths,
            "raw_scenarios": raw_scenarios,
            "no_interrupt": True,
        })

        _print_summary(group_name, result)

        group_result = {
            "group": group_name,
            "flow_card": result.get("stabilized_card") or result.get("flow_card"),
            "test_cases": result.get("test_cases", []),
            "exec_results": result.get("exec_results", []),
            "metrics": result.get("metrics", {}),
            "diagnoses": result.get("diagnoses", []),
            "validation_errors": result.get("validation_errors", []),
            "trace": result.get("trace", []),
        }
        all_results.append(group_result)

        # ── Артефакты группы ─────────────────────────────────────────────────
        group_slug = group["collection_name"].replace(" ", "_").replace("/", "-").replace("—", "-")
        group_out = out_dir / group_slug
        group_out.mkdir(exist_ok=True)

        stab_card = result.get("stabilized_card") or result.get("flow_card") or {}
        exec_results_all = result.get("exec_results", [])

        export_stabilization_trace(stab_card, exec_results_all, group_out / "stabilization_trace.md")
        export_coverage_report(
            result.get("metrics", {}), exec_results_all,
            group_out / "coverage_report.md", group_name,
        )
        tc_list = result.get("test_cases", [])
        if tc_list:
            export_test_cases_full(tc_list, group_out / "test_cases_full.md")

        if result.get("test_cases"):
            all_test_cases.extend(result["test_cases"])

        col = result.get("collection")
        if col:
            all_collections.append(col)

    # Объединённая коллекция
    if all_collections:
        combined_items: list[dict] = []
        combined_vars: list[dict] = []
        for col in all_collections:
            combined_items.extend(col.get("item", []))
            for v in col.get("variable", []):
                if not any(ev["key"] == v["key"] for ev in combined_vars):
                    combined_vars.append(v)

        combined_collection = {
            "info": {
                "name": "Bulletin Board API - Full Test Suite",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "variable": combined_vars,
            "item": combined_items,
        }
        col_path = out_dir / "bulletin_board_test_suite.json"
        export_collection(combined_collection, col_path)
        print(f"\n[OK] Combined collection: {col_path}")

    results_path = out_dir / "all_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"[OK] All results: {results_path}")

    md_path = out_dir / "all_test_cases.md"
    _export_md(all_test_cases, md_path)
    print(f"[OK] Test cases MD: {md_path}")

    if all_test_cases:
        tc_full_path = out_dir / "test_cases_full.md"
        export_test_cases_full(all_test_cases, tc_full_path)
        print(f"[OK] Test cases full: {tc_full_path}")

    combined_cov_path = out_dir / "coverage_report.md"
    export_combined_coverage(all_results, combined_cov_path)
    print(f"[OK] Combined coverage: {combined_cov_path}")

    print("\n" + "="*60)
    print("OVERALL SUMMARY — Bulletin Board")
    print("="*60)
    total_tc = len(all_test_cases)
    total_run = sum(len(r.get("exec_results", [])) for r in all_results)
    total_passed = sum(
        sum(1 for e in r.get("exec_results", []) if e.get("passed"))
        for r in all_results
    )
    print(f"  Groups run:   {len(all_results)}")
    print(f"  Test cases:   {total_tc}")
    if total_run:
        print(f"  Executed:     {total_passed}/{total_run} ({100*total_passed//total_run}%)")


if __name__ == "__main__":
    main()
