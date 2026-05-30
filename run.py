"""
Универсальный консольный runner системы тест-дизайна.

Читает конфигурацию из constants.json. Поддерживает:
  - запуск всех групп домена
  - запуск конкретных UC (с автоматическим объединением файлов группы)
  - запуск конкретной named group

Примеры:
  python run.py carsharing                          # все группы домена
  python run.py carsharing --uc UC_001 UC_002 UC_003  # конкретные UC вместе
  python run.py carsharing --group "UC_008: Cancel Reservation"
  python run.py bulletin_board
  python run.py bulletin_board --uc UC_103

Зависимости UC:
  UC, у которого requires=[...], должны гонять вместе с зависимостями ИЛИ
  требуемые данные должны быть в env_vars (seed-режим).
  При явном --uc передаются только указанные UC без автоматического добавления зависимостей.
  При --uc-with-deps зависимости добавляются автоматически (топологически).
"""

import argparse
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
from src.config import CONFIG, load_domain_config, load_constants
from src.graph import build_graph


def _topological_sort(ucs: list[str], all_scenarios: dict) -> list[str]:
    """Топологическая сортировка UC с учётом requires."""
    visited: set[str] = set()
    result: list[str] = []

    def visit(uc: str) -> None:
        if uc in visited:
            return
        visited.add(uc)
        for dep in all_scenarios.get(uc, {}).get("requires", []):
            visit(dep)
        result.append(uc)

    for uc in ucs:
        visit(uc)
    return result


def _export_md(test_cases: list[dict], path: Path) -> None:
    by_tech: dict[str, list[dict]] = {}
    for tc in test_cases:
        tech = tc.get("technique", "unknown")
        by_tech.setdefault(tech, []).append(tc)

    TECH_ORDER = ["happy_path", "boundary", "negative", "equivalence", "state_based"]
    lines = ["# Test Cases\n"]
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
    er = [e for e in result.get("exec_results", []) if "title" in e]
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
    for e in er:
        if not e.get("passed"):
            print(f"    FAIL [{e.get('technique','?')}] {e.get('title','?')} "
                  f"actual={e.get('actual_status')} exp={e.get('expected_status')}")


def _cli_human_review(interrupt_data: dict) -> list[dict]:
    """Интерактивный CLI-диалог для human-in-the-loop."""
    print("\n" + "="*60)
    print("HUMAN REVIEW REQUIRED")
    print("="*60)
    print(interrupt_data.get("message", ""))
    print()
    decisions: list[dict] = []
    for d in interrupt_data.get("diagnoses_needing_review", []):
        print(f"  [{d['case_id']}] step={d['step_id']}")
        print(f"  category={d['category']}, confidence={d['confidence']:.2f}")
        print(f"  evidence: {d['evidence']}")
        print(f"  reasoning: {d['reasoning'][:200]}")
        print()
        choice = input("  Decision [approve/reclassify/fix_card/skip] (default=approve): ").strip()
        if not choice:
            choice = "approve"
        note = input("  Note (optional): ").strip()
        dec: dict = {"case_id": d["case_id"], "step_id": d["step_id"], "decision": choice, "note": note}
        if choice == "reclassify":
            new_cat = input("  New category: ").strip()
            dec["new_category"] = new_cat
        decisions.append(dec)
    return decisions


def run_group(
    group_name: str,
    files: list[str],
    collection_name: str,
    graph,
    out_dir: Path,
    checkpointer=None,
) -> dict:
    """Запускает граф для одной группы сценариев и сохраняет артефакты."""
    spec_paths = [str(ROOT / f"data/scenarios/openapi_spec/{f}.yaml") for f in files]
    raw_scenarios = "\n\n".join(
        (ROOT / f"data/scenarios/{f}.md").read_text(encoding="utf-8")
        for f in files
    )
    CONFIG.collection_name = collection_name

    initial_state = {
        "spec_paths": spec_paths,
        "raw_scenarios": raw_scenarios,
        "no_interrupt": checkpointer is None,
    }

    if checkpointer is None:
        # Автоматический режим — interrupt пропускается внутри human_review узла
        result = graph.invoke(initial_state)
    else:
        # Интерактивный режим — обрабатываем interrupt
        from langgraph.types import Command
        import uuid
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        result = None
        state_to_resume = initial_state
        is_resume = False

        while True:
            interrupted = False
            for chunk in graph.stream(
                Command(resume=state_to_resume) if is_resume else state_to_resume,
                config=config,
                stream_mode="values",
            ):
                result = chunk  # последний chunk — финальное состояние

            # Проверяем interrupted state через state snapshot
            snap = graph.get_state(config)
            if snap.next:
                # Граф приостановлен
                interrupt_tasks = snap.tasks
                for task in interrupt_tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        for intr in task.interrupts:
                            decisions = _cli_human_review(intr.value)
                            state_to_resume = decisions
                            is_resume = True
                            interrupted = True
                            break

                if not interrupted:
                    break
            else:
                break

        if result is None:
            result = graph.get_state(config).values
    _print_summary(group_name, result)

    # ── Артефакты группы ─────────────────────────────────────────────────────
    group_slug = collection_name.replace(" ", "_").replace("/", "-").replace("—", "-")
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

    return {
        "group": group_name,
        "collection_name": collection_name,
        "flow_card": stab_card,
        "test_cases": tc_list,
        "exec_results": exec_results_all,
        "metrics": result.get("metrics", {}),
        "diagnoses": result.get("diagnoses", []),
        "validation_errors": result.get("validation_errors", []),
        "trace": result.get("trace", []),
        "collection": result.get("collection"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Design AI — консольный runner",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "domain",
        help="Имя домена из constants.json (например: carsharing, bulletin_board)",
    )
    parser.add_argument(
        "--uc",
        nargs="+",
        metavar="UC_ID",
        help="Запустить конкретные UC (например: UC_001 UC_002 UC_003). "
             "Файлы объединяются в одну группу.",
    )
    parser.add_argument(
        "--uc-with-deps",
        nargs="+",
        metavar="UC_ID",
        dest="uc_with_deps",
        help="Как --uc, но автоматически добавляет зависимости из requires.",
    )
    parser.add_argument(
        "--group",
        metavar="GROUP_NAME",
        help="Запустить конкретную named group из constants.json.",
    )
    parser.add_argument(
        "--output",
        default="output",
        metavar="DIR",
        help="Папка для сохранения артефактов (default: output).",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Отключить human-in-the-loop (автоматически approve все диагнозы).",
    )
    args = parser.parse_args()

    # Загрузка конфигурации домена
    try:
        domain_cfg = load_domain_config(args.domain)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    all_scenarios: dict = domain_cfg.get("scenarios", {})
    all_groups: list[dict] = domain_cfg.get("groups", [])

    # Определяем группы к запуску
    groups_to_run: list[dict] = []

    if args.uc:
        # Явный список UC → одна группа из их файлов
        files = []
        for uc in args.uc:
            sc = all_scenarios.get(uc)
            if not sc:
                print(f"ERROR: UC {uc!r} not found in constants.json for domain {args.domain!r}")
                sys.exit(1)
            files.extend(sc["files"])
        uc_label = " + ".join(args.uc)
        groups_to_run = [{
            "name": uc_label,
            "collection_name": f"{domain_cfg.get('collection_name', args.domain)} — {uc_label}",
            "files": files,
        }]

    elif args.uc_with_deps:
        # UC с автоматическим добавлением зависимостей
        ordered = _topological_sort(args.uc_with_deps, all_scenarios)
        files = []
        for uc in ordered:
            sc = all_scenarios.get(uc)
            if not sc:
                print(f"ERROR: UC {uc!r} not found")
                sys.exit(1)
            files.extend(sc["files"])
        uc_label = " + ".join(ordered)
        groups_to_run = [{
            "name": uc_label,
            "collection_name": f"{domain_cfg.get('collection_name', args.domain)} — {uc_label}",
            "files": files,
        }]

    elif args.group:
        # Конкретная named group
        found = [g for g in all_groups if g["name"] == args.group]
        if not found:
            available = [g["name"] for g in all_groups]
            print(f"ERROR: group {args.group!r} not found. Available: {available}")
            sys.exit(1)
        g = found[0]
        files = []
        for uc in g.get("ucs", []):
            sc = all_scenarios.get(uc)
            if sc:
                files.extend(sc["files"])
        groups_to_run = [{
            "name": g["name"],
            "collection_name": g.get("collection_name", g["name"]),
            "files": files,
        }]

    else:
        # Все группы домена
        for g in all_groups:
            files = []
            for uc in g.get("ucs", []):
                sc = all_scenarios.get(uc)
                if sc:
                    files.extend(sc["files"])
            groups_to_run.append({
                "name": g["name"],
                "collection_name": g.get("collection_name", g["name"]),
                "files": files,
            })

    if not groups_to_run:
        print("Nothing to run.")
        sys.exit(0)

    out_dir = ROOT / args.output
    out_dir.mkdir(exist_ok=True)

    # Human-in-the-loop: с checkpointer → интерактивный, без → автоматический
    interactive = not args.no_interactive if hasattr(args, "no_interactive") else True
    checkpointer = None
    if interactive:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        except ImportError:
            pass  # LangGraph без checkpointer → автоматический режим

    graph = build_graph(checkpointer=checkpointer)
    all_results: list[dict] = []
    all_collections: list[dict] = []
    all_test_cases: list[dict] = []

    for group in groups_to_run:
        print(f"\n{'='*60}")
        print(f"RUNNING: {group['name']}")
        print(f"Files:   {group['files']}")
        print('='*60)

        result = run_group(
            group_name=group["name"],
            files=group["files"],
            collection_name=group["collection_name"],
            graph=graph,
            out_dir=out_dir,
            checkpointer=checkpointer,
        )
        all_results.append(result)

        if result.get("test_cases"):
            all_test_cases.extend(result["test_cases"])
        if result.get("collection"):
            all_collections.append(result["collection"])

    # ── Объединённая коллекция ────────────────────────────────────────────────
    if all_collections:
        combined_items: list[dict] = []
        combined_vars: list[dict] = []
        for col in all_collections:
            combined_items.extend(col.get("item", []))
            for v in col.get("variable", []):
                if not any(ev["key"] == v["key"] for ev in combined_vars):
                    combined_vars.append(v)

        combined_col = {
            "info": {
                "name": domain_cfg.get("collection_name", args.domain),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "variable": combined_vars,
            "item": combined_items,
        }
        col_path = out_dir / f"{args.domain}_test_suite.json"
        export_collection(combined_col, col_path)
        print(f"\n[OK] Collection: {col_path}")

    # ── JSON всех результатов ─────────────────────────────────────────────────
    results_path = out_dir / "all_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"[OK] Results: {results_path}")

    # ── Тест-кейсы ────────────────────────────────────────────────────────────
    if all_test_cases:
        _export_md(all_test_cases, out_dir / "all_test_cases.md")
        export_test_cases_full(all_test_cases, out_dir / "test_cases_full.md")
        print(f"[OK] Test cases: {out_dir / 'test_cases_full.md'}")

    # ── Coverage report ───────────────────────────────────────────────────────
    export_combined_coverage(all_results, out_dir / "coverage_report.md")
    print(f"[OK] Coverage: {out_dir / 'coverage_report.md'}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_tc = len(all_test_cases)
    all_exec = [e for r in all_results for e in r.get("exec_results", []) if "title" in e]
    total_run = len(all_exec)
    total_pass = sum(1 for e in all_exec if e.get("passed"))

    print(f"\n{'='*60}\nOVERALL SUMMARY\n{'='*60}")
    print(f"  Domain:       {args.domain}")
    print(f"  Groups run:   {len(all_results)}")
    print(f"  Test cases:   {total_tc}")
    if total_run:
        print(f"  Executed:     {total_pass}/{total_run} ({100*total_pass//total_run}%)")


if __name__ == "__main__":
    main()
