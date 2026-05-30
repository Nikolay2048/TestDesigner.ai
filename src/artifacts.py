"""
Экспорт тестовых артефактов в файлы.

Принцип 3.1: чистый детерминированный Python, без LLM.

Генерируемые артефакты:
  test_cases_full.md     — тест-кейсы со всеми шагами, проверками, expected results
  test_cases_tms.csv     — CSV для импорта в TMS (TestRail/Xray/Zephyr совместимый)
  test_cases_allure.json — Allure-совместимый JSON для CI/CD
  coverage_report.md     — покрытие по endpoint'ам и техникам, pass rate
  stabilization_trace.md — лог попыток стабилизации happy-path
  defect_report.md       — выявленные дефекты (из Diagnosis)
  test_plan.md           — тест-план (scope, объём, риски)
  execution_report.md    — детальный отчёт о прогоне каждого тест-кейса
"""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path


def _norm_source(source: str) -> str:
    """'VarSource.STATIC' → 'static'"""
    if "." in str(source):
        return str(source).split(".")[-1].lower()
    return str(source)


# ──────────────────────── Test Cases Full Export ─────────────────────────────

def export_test_cases_full(test_cases: list[dict], path: Path) -> None:
    """
    Полный Markdown с тест-кейсами: каждый кейс включает
    setup-шаги, целевой шаг, assertions и ожидаемый результат.
    """
    by_tech: dict[str, list[dict]] = {}
    for tc in test_cases:
        tech = tc.get("technique", "unknown")
        if hasattr(tech, "value"):
            tech = tech.value
        by_tech.setdefault(tech, []).append(tc)

    TECH_ORDER = ["happy_path", "boundary", "negative", "equivalence", "state_based"]
    lines: list[str] = ["# Test Cases — Full Detail\n"]

    total = len(test_cases)
    lines.append(f"**Total:** {total} test cases\n")

    for tech in TECH_ORDER + [t for t in by_tech if t not in TECH_ORDER]:
        cases = by_tech.get(tech, [])
        if not cases:
            continue
        lines.append(f"\n---\n\n## {tech.replace('_', ' ').title()} ({len(cases)} cases)\n")

        for tc in cases:
            case_id = tc.get("case_id", "?")
            title = tc.get("title", "?")
            exp_status = tc.get("expected_status", "?")
            target = tc.get("target_step", "?")
            setup_chain = tc.get("setup_chain", [])
            modified_inputs = tc.get("modified_inputs", [])
            assertions = tc.get("assertions", [])
            group = tc.get("group", "")

            lines.append(f"### {title}\n")
            lines.append(f"- **ID:** `{case_id}`")
            lines.append(f"- **Technique:** {tech}")
            lines.append(f"- **Target step:** `{target}`")
            lines.append(f"- **Expected status:** `{exp_status}`")
            if group:
                lines.append(f"- **Group:** `{group}`")
            lines.append("")

            if setup_chain:
                lines.append("**Setup steps:**")
                for i, s in enumerate(setup_chain, 1):
                    op = s.get("operation_id", "?")
                    sid = s.get("step_id", "?")
                    inputs_summary = _inputs_summary(s.get("inputs", []))
                    lines.append(f"  {i}. `{sid}` → `{op}`{inputs_summary}")
                lines.append("")

            if modified_inputs:
                lines.append("**Modified inputs (target step):**")
                for inp in modified_inputs:
                    name = inp.get("name", "?")
                    source = _norm_source(inp.get("source", "?"))
                    val = inp.get("value") or inp.get("generator") or inp.get("source_field") or ""
                    lines.append(f"  - `{name}`: source=`{source}` value=`{val}`")
                lines.append("")

            if assertions:
                lines.append("**Assertions:**")
                for a in assertions:
                    atype = a.get("type", "?")
                    if atype == "status_code":
                        if "expected" in a:
                            lines.append(f"  - Status code == `{a['expected']}`")
                        elif "expected_range" in a:
                            lo, hi = a["expected_range"]
                            lines.append(f"  - Status code in `{lo}–{hi}`")
                    else:
                        lines.append(f"  - {a}")
                lines.append("")

            lines.append(f"**Expected result:** HTTP {exp_status} — "
                         f"{'pass' if 200 <= int(exp_status) < 300 else 'expected error'}\n")

    path.write_text("\n".join(lines), encoding="utf-8")


def _inputs_summary(inputs: list[dict]) -> str:
    if not inputs:
        return ""
    parts = []
    for inp in inputs[:4]:
        name = inp.get("name", "?")
        src = _norm_source(inp.get("source", "?"))
        parts.append(f"{name}({src})")
    suffix = f"+{len(inputs)-4} more" if len(inputs) > 4 else ""
    return " [" + ", ".join(parts) + (", " + suffix if suffix else "") + "]"


# ──────────────────────── Coverage Report ────────────────────────────────────

def export_coverage_report(
    metrics: dict,
    exec_results: list[dict],
    path: Path,
    group_name: str = "",
) -> None:
    """
    Markdown-отчёт о покрытии: endpoint coverage, техники, pass rate, упавшие кейсы.
    """
    lines: list[str] = [f"# Coverage Report{f' — {group_name}' if group_name else ''}\n"]

    stab = metrics.get("stabilization", {})
    ep = metrics.get("endpoints", {})
    tc = metrics.get("test_cases", {})
    exe = metrics.get("execution", {})
    diag = metrics.get("diagnosis", {})

    # Stabilization
    lines.append("## Stabilization\n")
    is_stab = stab.get("is_stabilized", False)
    fixes = stab.get("total_fixes_applied", 0)
    lines.append(f"- **Status:** {'✓ Stabilized' if is_stab else '✗ Not stabilized'}")
    lines.append(f"- **Fixes applied:** {fixes}\n")

    # Endpoint Coverage
    lines.append("## Endpoint Coverage\n")
    total_ep = ep.get("total", 0)
    covered_ep = ep.get("covered", 0)
    pct = ep.get("coverage_pct", 0.0)
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total endpoints | {total_ep} |")
    lines.append(f"| Covered | {covered_ep} |")
    lines.append(f"| Coverage | **{pct}%** |")
    uncovered = ep.get("uncovered", [])
    if uncovered:
        lines.append(f"\n**Uncovered endpoints:** {', '.join(f'`{u}`' for u in uncovered)}")
    lines.append("")

    # Test Cases
    lines.append("## Test Cases\n")
    lines.append(f"**Total generated:** {tc.get('total', 0)}\n")
    by_tech = tc.get("by_technique", {})
    if by_tech:
        lines.append("| Technique | Count |")
        lines.append("|-----------|-------|")
        for tech, n in sorted(by_tech.items()):
            lines.append(f"| {tech.replace('_', ' ').title()} | {n} |")
        lines.append("")
    by_ep = tc.get("by_endpoint", {})
    if by_ep:
        lines.append("| Endpoint | Test Cases |")
        lines.append("|----------|-----------|")
        for op, n in sorted(by_ep.items()):
            lines.append(f"| `{op}` | {n} |")
        lines.append("")

    # Execution Results
    lines.append("## Execution Results (Phase 2)\n")
    total_run = exe.get("total_run", 0)
    if total_run == 0:
        lines.append("_Phase 2 not executed (stabilization failed or no test cases)._\n")
    else:
        passed = exe.get("passed", 0)
        failed = exe.get("failed", 0)
        rate = exe.get("pass_rate_pct", 0.0)
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total run | {total_run} |")
        lines.append(f"| Passed | {passed} |")
        lines.append(f"| Failed | {failed} |")
        lines.append(f"| Pass rate | **{rate}%** |")
        lines.append("")

        failed_cases = exe.get("failed_cases", [])
        if failed_cases:
            lines.append("**Failed cases:**\n")
            # Дополним из exec_results для деталей
            result_map = {r.get("case_id"): r for r in exec_results}
            for fc in failed_cases:
                cid = fc.get("case_id", "?")
                title = fc.get("title", "?")
                detail = result_map.get(cid, {})
                actual = detail.get("actual_status", "?")
                expected = detail.get("expected_status", "?")
                tech = detail.get("technique", "?")
                lines.append(f"- [`{tech}`] **{title}**")
                lines.append(f"  - Expected: `{expected}` | Actual: `{actual}`")
                reason = fc.get("reason", "") or detail.get("failure_reason", "")
                if reason:
                    lines.append(f"  - Reason: {reason}")
            lines.append("")

    # Diagnosis
    if diag.get("total", 0) > 0:
        lines.append("## Diagnosis\n")
        lines.append(f"**Total diagnoses:** {diag['total']}")
        by_cat = diag.get("by_category", {})
        for cat, n in by_cat.items():
            lines.append(f"- `{cat}`: {n}")
        nh = diag.get("needs_human", 0)
        if nh:
            lines.append(f"\n**Needs human review:** {nh}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────── Stabilization Trace ────────────────────────────────

def export_stabilization_trace(
    stabilized_card: dict,
    exec_results: list[dict],
    path: Path,
) -> None:
    """
    Markdown-трейс попыток стабилизации happy-path:
    для каждого шага — все попытки, HTTP-ответы и применённые фиксы.
    """
    lines: list[str] = ["# Stabilization Trace\n"]

    flow_id = stabilized_card.get("flow_id", "?")
    flow_name = stabilized_card.get("name", "?")
    is_stab = stabilized_card.get("is_stabilized", False)
    stab_log = stabilized_card.get("stabilization_log", [])

    lines.append(f"**Flow:** `{flow_id}` — {flow_name}")
    lines.append(f"**Result:** {'✓ Stabilized' if is_stab else '✗ NOT stabilized'}\n")

    # Шаги FlowCard
    steps = stabilized_card.get("steps", [])
    if steps:
        lines.append("## Steps (Final)\n")
        for step in steps:
            sid = step.get("step_id", "?")
            op = step.get("operation_id", "?")
            inputs = step.get("inputs", [])
            lines.append(f"### {sid} → `{op}`\n")
            if inputs:
                lines.append("**Inputs:**")
                for inp in inputs:
                    name = inp.get("name", "?")
                    source = _norm_source(inp.get("source", "?"))
                    loc = inp.get("target_location", "?")
                    val = inp.get("value") or inp.get("generator") or inp.get("source_field") or ""
                    lines.append(f"  - `{name}`: source=`{source}`, location=`{loc}`, value=`{val}`")
                lines.append("")

    # Лог фиксов
    if stab_log:
        lines.append("## Stabilization Fixes Applied\n")
        for entry in stab_log:
            sid = entry.get("step_id", "?")
            attempt = entry.get("attempt", "?")
            reasoning = entry.get("reasoning", "")
            fixes = entry.get("fixes", [])
            lines.append(f"### {sid} (attempt {attempt})\n")
            if reasoning:
                lines.append(f"**Reasoning:** {reasoning}\n")
            if fixes:
                lines.append("**Fixes:**")
                for f in fixes:
                    fname = f.get("name", "?")
                    new_val = f.get("new_value", "")
                    new_src = f.get("new_source", "")
                    expl = f.get("explanation", "")
                    lines.append(f"  - `{fname}`: source=`{new_src}`, value=`{new_val}`")
                    if expl:
                        lines.append(f"    _{expl}_")
                lines.append("")

    # HTTP-лог всех попыток (из phase 1 exec results)
    phase1 = [r for r in exec_results if "title" not in r]
    if phase1:
        lines.append("## HTTP Attempt Log (Phase 1)\n")
        for result in phase1:
            cid = result.get("case_id", "?")
            passed = result.get("passed", False)
            icon = "✓" if passed else "✗"
            lines.append(f"### {icon} {cid}\n")
            for step_log in result.get("steps_log", []):
                attempt = step_log.get("stabilize_attempt", 1)
                sid = step_log.get("step_id", "?")
                method = step_log.get("method", "")
                url = step_log.get("url", "")
                status = step_log.get("status_code", "?")
                step_pass = step_log.get("passed", False)
                step_icon = "✓" if step_pass else "✗"
                lines.append(f"- {step_icon} **{sid}** (attempt {attempt}): `{method} {url}` → `{status}`")
                if not step_pass:
                    err = step_log.get("error", "")
                    detail = step_log.get("details", "")
                    if err:
                        lines.append(f"  - error: `{err}`")
                    if detail:
                        detail_str = str(detail)[:200]
                        lines.append(f"  - details: {detail_str}")
                    resp = step_log.get("response", {})
                    if resp and isinstance(resp, dict):
                        msg = resp.get("message", "") or resp.get("errorCode", "")
                        if msg:
                            lines.append(f"  - response: `{msg}`")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────── Combined report (multi-group) ──────────────────────

def export_combined_coverage(all_results: list[dict], path: Path) -> None:
    """
    Сводный coverage report по всем группам сценариев.
    """
    lines: list[str] = ["# Combined Coverage Report\n"]

    total_tc = sum(len(r.get("test_cases", [])) for r in all_results)
    all_exec = [e for r in all_results for e in r.get("exec_results", []) if "title" in e]
    total_run = len(all_exec)
    total_pass = sum(1 for e in all_exec if e.get("passed"))

    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Groups run | {len(all_results)} |")
    lines.append(f"| Test cases total | {total_tc} |")
    if total_run:
        rate = round(100.0 * total_pass / total_run, 1)
        lines.append(f"| Executed | {total_run} |")
        lines.append(f"| Passed | {total_pass} |")
        lines.append(f"| Overall pass rate | **{rate}%** |")
    lines.append("")

    lines.append("## Per-Group Summary\n")
    lines.append("| Group | Stabilized | Test Cases | Passed | Failed | Pass Rate |")
    lines.append("|-------|-----------|-----------|--------|--------|-----------|")

    for r in all_results:
        name = r.get("group", "?")
        m = r.get("metrics", {})
        stab = m.get("stabilization", {}).get("is_stabilized", False)
        tc_n = len(r.get("test_cases", []))
        exec_r = [e for e in r.get("exec_results", []) if "title" in e]
        p = sum(1 for e in exec_r if e.get("passed"))
        f = len(exec_r) - p
        rate_str = f"{round(100.0*p/len(exec_r),1)}%" if exec_r else "—"
        stab_str = "✓" if stab else "✗"
        lines.append(f"| {name} | {stab_str} | {tc_n} | {p} | {f} | {rate_str} |")

    lines.append("")

    # Endpoint coverage per group
    lines.append("## Endpoint Coverage by Group\n")
    for r in all_results:
        name = r.get("group", "?")
        ep = r.get("metrics", {}).get("endpoints", {})
        total_ep = ep.get("total", 0)
        covered = ep.get("covered", 0)
        pct = ep.get("coverage_pct", 0.0)
        uncov = ep.get("uncovered", [])
        lines.append(f"**{name}:** {covered}/{total_ep} ({pct}%)")
        if uncov:
            lines.append(f"  Uncovered: {', '.join(f'`{u}`' for u in uncov)}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────── TMS CSV Export (TestRail / Xray / Zephyr) ──────────

def export_test_cases_csv(test_cases: list[dict], path: Path) -> None:
    """
    CSV для импорта в TMS (TestRail, Xray, Zephyr, Jira).

    Колонки совместимы с TestRail CSV import format:
      Title, Suite, Type, Priority, Preconditions, Steps, Expected Result, Automation Status
    """
    TECH_TO_TYPE = {
        "happy_path":  "Positive",
        "boundary":    "Boundary",
        "negative":    "Negative",
        "equivalence": "Positive",
        "state_based": "Negative",
    }
    TECH_TO_PRIORITY = {
        "happy_path":  "Critical",
        "boundary":    "High",
        "negative":    "High",
        "equivalence": "Medium",
        "state_based": "High",
    }

    rows = []
    for tc in test_cases:
        tech = tc.get("technique", "unknown")
        if hasattr(tech, "value"):
            tech = tech.value
        if "." in str(tech):
            tech = str(tech).split(".")[-1].lower()

        setup_steps = tc.get("setup_chain", [])
        modified_inputs = tc.get("modified_inputs", [])
        assertions = tc.get("assertions", [])
        exp_status = tc.get("expected_status", "?")
        group = tc.get("group", "")
        target = tc.get("target_step", "?")

        # Preconditions: список setup-шагов
        preconditions_parts = []
        for s in setup_steps:
            op = s.get("operation_id", "?")
            method = ""
            for inp in s.get("inputs", []):
                loc = inp.get("target_location", "")
                if loc.startswith("path."):
                    pass  # будет в op
            preconditions_parts.append(f"Run {op} (setup)")
        preconditions = "; ".join(preconditions_parts) if preconditions_parts else "None"

        # Steps: собираем в формат "Step|Expected"
        steps_lines = []
        for i, inp in enumerate(modified_inputs, 1):
            name = inp.get("name", "?")
            src = _norm_source(inp.get("source", "?"))
            val = inp.get("value") or inp.get("generator") or inp.get("source_field") or ""
            loc = inp.get("target_location", "")
            steps_lines.append(f"{i}. Send {loc}={name} (source={src}, value={val})")

        # Expected result from assertions
        exp_parts = []
        for a in assertions:
            if a.get("type") == "status_code":
                if "expected" in a:
                    exp_parts.append(f"HTTP {a['expected']}")
                elif "expected_range" in a:
                    lo, hi = a["expected_range"]
                    exp_parts.append(f"HTTP {lo}-{hi}")
        expected = ", ".join(exp_parts) if exp_parts else f"HTTP {exp_status}"

        # Suite from group: "flow_id/endpoint/technique" → "endpoint"
        suite_parts = group.split("/")
        suite = suite_parts[1] if len(suite_parts) >= 2 else group

        rows.append({
            "ID": tc.get("case_id", ""),
            "Title": tc.get("title", ""),
            "Suite": suite,
            "Type": TECH_TO_TYPE.get(tech, "Functional"),
            "Priority": TECH_TO_PRIORITY.get(tech, "Medium"),
            "Preconditions": preconditions,
            "Steps": "\n".join(steps_lines),
            "Expected Result": expected,
            "Technique": tech,
            "Target Step": target,
            "Automation Status": "Automated",
        })

    fieldnames = ["ID", "Title", "Suite", "Type", "Priority", "Preconditions",
                  "Steps", "Expected Result", "Technique", "Target Step", "Automation Status"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ──────────────────────── Allure Results JSON ─────────────────────────────────

def export_allure_results(
    test_cases: list[dict],
    exec_results: list[dict],
    path: Path,
) -> None:
    """
    Генерирует Allure-совместимые JSON результаты.
    Каждый тест-кейс = отдельный файл *-result.json в директории path/.

    Формат: https://docs.qameta.io/allure/#_test_result
    """
    path.mkdir(parents=True, exist_ok=True)

    result_map = {r.get("case_id"): r for r in exec_results if "title" in r}

    TECH_LABELS = {
        "happy_path": "happy_path",
        "boundary": "boundary",
        "negative": "negative",
        "equivalence": "equivalence",
        "state_based": "state_based",
    }

    for tc in test_cases:
        case_id = tc.get("case_id", str(uuid.uuid4()))
        title = tc.get("title", "?")
        tech = tc.get("technique", "unknown")
        if hasattr(tech, "value"):
            tech = tech.value
        if "." in str(tech):
            tech = str(tech).split(".")[-1].lower()

        exec_res = result_map.get(case_id)
        if exec_res:
            passed = exec_res.get("passed", False)
            actual = exec_res.get("actual_status")
            expected = exec_res.get("expected_status")
            status = "passed" if passed else "failed"
            stop_time = 0
        else:
            status = "skipped"
            actual = None
            expected = tc.get("expected_status")
            stop_time = 0

        result = {
            "uuid": str(uuid.uuid4()),
            "historyId": case_id,
            "name": title,
            "status": status,
            "labels": [
                {"name": "suite", "value": tc.get("group", "").split("/")[1] if "/" in tc.get("group", "") else ""},
                {"name": "tag", "value": TECH_LABELS.get(tech, tech)},
                {"name": "feature", "value": tc.get("flow_id", "")},
                {"name": "story", "value": title},
            ],
            "steps": [],
            "start": 0,
            "stop": stop_time,
        }

        if status == "failed" and exec_res:
            fr = exec_res.get("failure_reason", "")
            result["statusDetails"] = {
                "message": f"Expected: {expected}, Actual: {actual}",
                "trace": fr or "",
            }

        # Steps from setup_chain + modified_inputs
        allure_steps = []
        for s in tc.get("setup_chain", []):
            allure_steps.append({
                "name": f"[setup] {s.get('operation_id', '?')}",
                "status": "passed",
                "steps": [],
            })
        allure_steps.append({
            "name": f"[target] {tc.get('target_step', '?')}",
            "status": status,
            "steps": [],
        })
        result["steps"] = allure_steps

        filename = f"{case_id}-result.json"
        with open(path / filename, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)


# ──────────────────────── Defect Report ──────────────────────────────────────

def export_defect_report(
    diagnoses: list[dict],
    exec_results: list[dict],
    path: Path,
    group_name: str = "",
) -> None:
    """
    Отчёт об обнаруженных дефектах из Diagnosis.
    SUSPECTED_SERVICE_BUG + needs_human=True → потенциальный баг сервиса.
    """
    lines = [f"# Defect Report{f' — {group_name}' if group_name else ''}\n"]

    bugs = [d for d in diagnoses if d.get("category") in ("service_bug", "DiagnosisCategory.SUSPECTED_SERVICE_BUG")]
    uncertain = [d for d in diagnoses if d.get("needs_human") and d.get("category") not in ("service_bug", "DiagnosisCategory.SUSPECTED_SERVICE_BUG")]
    all_issues = bugs + uncertain

    if not all_issues:
        lines.append("_No defects or issues requiring human review were detected._\n")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append(f"**Total issues:** {len(all_issues)} ({len(bugs)} suspected bugs, {len(uncertain)} uncertain)\n")

    if bugs:
        lines.append("## Suspected Service Bugs\n")
        lines.append("> These requests contained valid data per spec but the server rejected them.\n")
        for i, d in enumerate(bugs, 1):
            lines.append(f"### Bug #{i}: {d.get('step_id', '?')}\n")
            lines.append(f"- **Case:** `{d.get('case_id', '?')}`")
            lines.append(f"- **Step:** `{d.get('step_id', '?')}`")
            lines.append(f"- **Confidence:** {d.get('confidence', 0):.0%}")
            lines.append(f"- **Evidence:** {d.get('evidence', [])}")
            lines.append(f"- **What changed:** {d.get('what_changed', {})}")
            reasoning = d.get("reasoning", "")
            if reasoning:
                lines.append(f"\n**Analysis:** {reasoning}\n")
            human_dec = d.get("human_decision")
            if human_dec:
                lines.append(f"**Human decision:** {human_dec} — {d.get('human_note', '')}")
            lines.append("")

    if uncertain:
        lines.append("## Requires Human Review\n")
        for d in uncertain:
            cat = _norm_source(d.get("category", "?"))
            lines.append(f"- `{d.get('step_id', '?')}` ({cat}, confidence={d.get('confidence', 0):.0%}): {d.get('reasoning', '')[:150]}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────── Test Plan ──────────────────────────────────────────

def export_test_plan(
    group_name: str,
    endpoints: list[dict],
    test_cases: list[dict],
    flow_card: dict,
    path: Path,
) -> None:
    """
    Тест-план: scope, цели, стратегия, риски, покрытие.
    Генерируется детерминированно из данных пайплайна.
    """
    steps = flow_card.get("steps", [])
    tech_counts: dict[str, int] = {}
    for tc in test_cases:
        t = tc.get("technique", "unknown")
        if hasattr(t, "value"):
            t = t.value
        if "." in str(t):
            t = str(t).split(".")[-1].lower()
        tech_counts[t] = tech_counts.get(t, 0) + 1

    lines = [f"# Test Plan: {group_name}\n"]

    lines.append("## 1. Scope\n")
    lines.append(f"This test plan covers the **{group_name}** scenario.")
    lines.append(f"The scenario consists of **{len(steps)} steps** across **{len(endpoints)} API endpoints**.\n")

    lines.append("## 2. Endpoints Under Test\n")
    lines.append("| # | Method | Path | Operation |")
    lines.append("|---|--------|------|-----------|")
    for i, ep in enumerate(endpoints, 1):
        lines.append(f"| {i} | `{ep.get('method')}` | `{ep.get('path')}` | `{ep.get('operation_id')}` |")
    lines.append("")

    lines.append("## 3. Test Strategy\n")
    lines.append("The following test design techniques are applied:\n")
    TECH_DESC = {
        "happy_path":  "Happy Path — verify the nominal flow passes end-to-end",
        "boundary":    "Boundary Value Analysis — test min/max/enum limits from the spec",
        "negative":    "Negative Testing — missing required fields, invalid values",
        "equivalence": "Equivalence Partitioning — semantic classes not captured by schema",
        "state_based": "State Transition — out-of-order operations, double operations",
    }
    for tech, cnt in sorted(tech_counts.items()):
        desc = TECH_DESC.get(tech, tech)
        lines.append(f"- **{desc}** — {cnt} test case(s)")
    lines.append("")

    lines.append("## 4. Test Cases Summary\n")
    lines.append(f"**Total test cases:** {len(test_cases)}\n")
    lines.append("| Technique | Count |")
    lines.append("|-----------|-------|")
    for tech, cnt in sorted(tech_counts.items()):
        lines.append(f"| {tech.replace('_', ' ').title()} | {cnt} |")
    lines.append("")

    lines.append("## 5. Entry / Exit Criteria\n")
    lines.append("**Entry:**")
    lines.append("- Mock server (or real service) is running and responding")
    lines.append("- Environment variables (seed IDs) are configured\n")
    lines.append("**Exit:**")
    lines.append("- All happy path tests pass")
    lines.append("- All suspected service bugs are investigated")
    lines.append("- Pass rate ≥ 80% for positive scenarios\n")

    lines.append("## 6. Risks\n")
    lines.append("| Risk | Mitigation |")
    lines.append("|------|-----------|")
    lines.append("| Service state changes between test runs | Mock server reset before each test case |")
    lines.append("| LLM generates incorrect test data | Stabilizer agent corrects data automatically |")
    lines.append("| Spec gaps (undocumented constraints) | Diagnosis Layer 2 identifies spec gaps |")
    lines.append("")

    is_stab = flow_card.get("is_stabilized", False)
    lines.append("## 7. Test Environment\n")
    lines.append(f"- **Happy path stabilized:** {'Yes' if is_stab else 'No'}")
    lines.append(f"- **Scenario steps:** {len(steps)}")
    lines.append(f"- **Flow ID:** `{flow_card.get('flow_id', '?')}`")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────── Execution Report ───────────────────────────────────

def export_execution_report(
    test_cases: list[dict],
    exec_results: list[dict],
    path: Path,
    group_name: str = "",
) -> None:
    """
    Детальный отчёт о прогоне: каждый тест-кейс с фактическим vs ожидаемым результатом.
    Включает HTTP-запросы/ответы для упавших кейсов.
    """
    lines = [f"# Execution Report{f' — {group_name}' if group_name else ''}\n"]

    result_map = {r.get("case_id"): r for r in exec_results if "title" in r}
    phase1 = [r for r in exec_results if "title" not in r]

    if phase1:
        r1 = phase1[0]
        icon = "✓" if r1.get("passed") else "✗"
        fixes = r1.get("total_fixes_applied", 0)
        lines.append(f"## Phase 1 — Happy Path Stabilization\n")
        lines.append(f"**Result:** {icon} {'Passed' if r1.get('passed') else 'Failed'} ({fixes} fixes applied)\n")

    if not result_map:
        lines.append("## Phase 2 — Test Case Execution\n_Not executed._\n")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    passed_cases = [r for r in result_map.values() if r.get("passed")]
    failed_cases = [r for r in result_map.values() if not r.get("passed")]
    skipped = len(test_cases) - len(result_map)
    total = len(result_map)

    lines.append("## Phase 2 — Test Case Execution\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total | {total} |")
    lines.append(f"| Passed | {len(passed_cases)} ✓ |")
    lines.append(f"| Failed | {len(failed_cases)} ✗ |")
    lines.append(f"| Skipped | {skipped} |")
    if total > 0:
        rate = round(100.0 * len(passed_cases) / total, 1)
        lines.append(f"| Pass Rate | **{rate}%** |")
    lines.append("")

    if failed_cases:
        lines.append("## Failed Test Cases\n")
        for r in failed_cases:
            cid = r.get("case_id", "?")
            title = r.get("title", "?")
            actual = r.get("actual_status", "?")
            expected = r.get("expected_status", "?")
            tech = r.get("technique", "?")
            reason = r.get("failure_reason", "")

            lines.append(f"### ✗ {title}\n")
            lines.append(f"- **ID:** `{cid}`")
            lines.append(f"- **Technique:** {tech}")
            lines.append(f"- **Expected status:** `{expected}`")
            lines.append(f"- **Actual status:** `{actual}`")
            if reason:
                lines.append(f"- **Failure reason:** {reason}")

            # Показываем последний шаг с ошибкой
            steps_log = r.get("steps_log", [])
            for slog in reversed(steps_log):
                if not slog.get("passed"):
                    method = slog.get("method", "")
                    url = slog.get("url", "")
                    resp = slog.get("response", {})
                    if url:
                        lines.append(f"- **Failed request:** `{method} {url}`")
                    if resp and isinstance(resp, dict):
                        msg = resp.get("message", "") or resp.get("errorCode", "")
                        if msg:
                            lines.append(f"- **Server response:** `{msg}`")
                    break
            lines.append("")

    if passed_cases:
        lines.append("## Passed Test Cases\n")
        for r in passed_cases:
            cid = r.get("case_id", "?")
            title = r.get("title", "?")
            actual = r.get("actual_status", "?")
            tech = r.get("technique", "?")
            lines.append(f"- ✓ `[{tech}]` **{title}** — HTTP `{actual}`")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
