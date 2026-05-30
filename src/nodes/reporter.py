"""
Reporter — Этап 9: метрики и отчёт для человека.

Источники данных (все из state):
  endpoints          → полный список endpoint'ов из спеки
  test_cases         → сгенерированные кейсы (от test_designer)
  exec_results       → результаты прогона (от executor_run_all; может быть пуст)
  stabilized_card    → результат стабилизации happy-path
  diagnoses          → диагностика по упавшим стабилизациям

Вычисляется детерминированным кодом, LLM не нужен.
"""

from src.state import GraphState


# ─────────────── Helpers ────────────────────────────────────────────────────

def _op_id_from_group(group: str) -> str:
    """
    Извлекает operation_id из поля group.
    Формат: "{flow_id}/{operation_id}/{technique}"
    """
    parts = group.split("/")
    return parts[1] if len(parts) >= 2 else ""


def _endpoint_coverage(
    endpoints: list[dict],
    test_cases: list[dict],
) -> dict:
    all_ops = {ep["operation_id"] for ep in endpoints}
    covered_ops = {_op_id_from_group(tc.get("group", "")) for tc in test_cases} - {""}
    uncovered = sorted(all_ops - covered_ops)
    total = len(all_ops)
    covered = len(all_ops & covered_ops)
    pct = round(100.0 * covered / total, 1) if total else 0.0
    return {
        "total": total,
        "covered": covered,
        "uncovered": uncovered,
        "coverage_pct": pct,
    }


def _test_case_breakdown(test_cases: list[dict]) -> dict:
    by_technique: dict[str, int] = {}
    by_endpoint: dict[str, int] = {}
    for tc in test_cases:
        raw = tc.get("technique", "unknown")
        tech = raw.value if hasattr(raw, "value") else str(raw)
        # normalise "TestTechnique.BOUNDARY" → "boundary"
        if "." in tech:
            tech = tech.split(".")[-1].lower()
        by_technique[tech] = by_technique.get(tech, 0) + 1
        op = _op_id_from_group(tc.get("group", ""))
        if op:
            by_endpoint[op] = by_endpoint.get(op, 0) + 1
    return {
        "total": len(test_cases),
        "by_technique": dict(sorted(by_technique.items())),
        "by_endpoint": dict(sorted(by_endpoint.items())),
    }


def _execution_summary(exec_results: list[dict]) -> dict:
    phase2 = [r for r in exec_results if "title" in r]  # Phase 2 results have 'title'
    if not phase2:
        return {"total_run": 0, "passed": 0, "failed": 0, "pass_rate_pct": None}
    passed = sum(1 for r in phase2 if r.get("passed"))
    failed = len(phase2) - passed
    rate = round(100.0 * passed / len(phase2), 1) if phase2 else 0.0
    failed_cases = [
        {"case_id": r["case_id"], "title": r.get("title", ""), "reason": r.get("failure_reason", "")}
        for r in phase2 if not r.get("passed")
    ]
    return {
        "total_run": len(phase2),
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": rate,
        "failed_cases": failed_cases,
    }


def _diagnosis_summary(diagnoses: list[dict]) -> dict:
    by_category: dict[str, int] = {}
    needs_human = 0
    for d in diagnoses:
        cat = d.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        if d.get("needs_human"):
            needs_human += 1
    return {
        "total": len(diagnoses),
        "by_category": dict(sorted(by_category.items())),
        "needs_human": needs_human,
    }


def _stabilization_summary(stabilized_card: dict, exec_results: list[dict]) -> dict:
    # Phase 1 exec result: the one without 'title' (produced by executor_stabilize)
    phase1 = [r for r in exec_results if "title" not in r]
    total_fixes = sum(r.get("total_fixes_applied", 0) for r in phase1)
    return {
        "is_stabilized": stabilized_card.get("is_stabilized", False),
        "total_fixes_applied": total_fixes,
        "flow_id": stabilized_card.get("flow_id", ""),
        "flow_name": stabilized_card.get("name", ""),
    }


# ─────────────── Report text renderer ───────────────────────────────────────

def _render_report(
    stab: dict,
    coverage: dict,
    cases: dict,
    execution: dict,
    diag: dict,
) -> str:
    lines = ["=" * 56, "TEST DESIGN REPORT", "=" * 56, ""]

    # Flow
    flow_status = "STABILIZED" if stab["is_stabilized"] else "NOT STABILIZED"
    lines.append(f"FLOW: {stab['flow_id']} — {stab['flow_name']}")
    lines.append(f"  Stabilization: {flow_status} ({stab['total_fixes_applied']} fix(es) applied)")
    lines.append("")

    # Endpoint coverage
    lines.append("ENDPOINT COVERAGE")
    lines.append(f"  Total:    {coverage['total']}")
    lines.append(f"  Covered:  {coverage['covered']} ({coverage['coverage_pct']}%)")
    if coverage["uncovered"]:
        lines.append(f"  Uncovered: {', '.join(coverage['uncovered'])}")
    lines.append("")

    # Test cases
    lines.append(f"TEST CASES GENERATED: {cases['total']}")
    for tech, n in cases["by_technique"].items():
        lines.append(f"  {tech:<16} {n}")
    lines.append("")
    if cases["by_endpoint"]:
        lines.append("BY ENDPOINT")
        for op, n in cases["by_endpoint"].items():
            lines.append(f"  {op:<36} {n}")
        lines.append("")

    # Execution
    lines.append("EXECUTION (Phase 2)")
    if execution["total_run"] == 0:
        lines.append("  Not run yet.")
    else:
        lines.append(f"  Total:   {execution['total_run']}")
        lines.append(f"  Passed:  {execution['passed']}")
        lines.append(f"  Failed:  {execution['failed']}  (pass rate: {execution['pass_rate_pct']}%)")
        if execution.get("failed_cases"):
            lines.append("  Failed cases:")
            for fc in execution["failed_cases"][:10]:
                lines.append(f"    - {fc['case_id']}: {fc['title']}")
    lines.append("")

    # Diagnosis
    lines.append("DIAGNOSIS")
    if diag["total"] == 0:
        lines.append("  No stabilization fixes recorded.")
    else:
        lines.append(f"  Total fixes diagnosed: {diag['total']}")
        for cat, n in diag["by_category"].items():
            lines.append(f"    {cat}: {n}")
        lines.append(f"  Needs human review: {diag['needs_human']}")
    lines.append("")
    lines.append("=" * 56)
    return "\n".join(lines)


# ─────────────── Main node ──────────────────────────────────────────────────

def reporter(state: GraphState) -> dict:
    endpoints: list[dict] = state.get("endpoints", [])
    test_cases: list[dict] = state.get("test_cases", [])
    exec_results: list[dict] = state.get("exec_results", [])
    stabilized_card: dict = state.get("stabilized_card", {})
    diagnoses: list[dict] = state.get("diagnoses", [])

    coverage = _endpoint_coverage(endpoints, test_cases)
    cases = _test_case_breakdown(test_cases)
    execution = _execution_summary(exec_results)
    diag = _diagnosis_summary(diagnoses)
    stab = _stabilization_summary(stabilized_card, exec_results)

    metrics = {
        "stabilization": stab,
        "endpoints": coverage,
        "test_cases": cases,
        "execution": execution,
        "diagnosis": diag,
    }

    report_text = _render_report(stab, coverage, cases, execution, diag)
    print(report_text)

    return {
        "metrics": metrics,
        "trace": ["reporter"],
    }
