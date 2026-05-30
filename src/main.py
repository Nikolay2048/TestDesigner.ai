"""
Точка входа — доменная конфигурация и запуск графа.

Всё специфичное для предметной области (список сценариев, URL сервиса, seed env-vars,
имя коллекции) задаётся ЗДЕСЬ и нигде больше. Граф и узлы предметной области не знают.

Чтобы переключить на другой проект:
  1. Изменить ALL_SCENARIOS (имена .md / .yaml файлов)
  2. Изменить CONFIG (base_url, env_vars, collection_name)
  3. Изменить OUTPUT_COLLECTION
"""

import json
from pathlib import Path

from src.collection_builder import export_collection
from src.config import CONFIG
from src.graph import build_graph

# ── Доменная конфигурация ────────────────────────────────────────────────────

ALL_SCENARIOS = [
    "UC_001_SearchAvailableCars",
    "UC_002_CreateReservationDraft",
    "UC_003_ConfirmReservation",
    "UC_004_StartRental",
    "UC_005_ExtendRental",
    "UC_006_ReportDamage",
    "UC_007_CompleteRental",
    "UC_008_CancelReservation",
]

CONFIG.base_url = "http://localhost:8000"
CONFIG.env_vars = {
    "customerId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "paymentId":  "99999999-9999-9999-9999-999999999999",
}
CONFIG.collection_name = "Carsharing API — Full Test Suite"

OUTPUT_COLLECTION = Path("output/carsharing_test_suite.json")

# ── Входные данные для графа ─────────────────────────────────────────────────

SPEC_PATHS = [
    f"data/scenarios/openapi_spec/{name}.yaml"
    for name in ALL_SCENARIOS
]

RAW_SCENARIOS = "\n\n".join(
    Path(f"data/scenarios/{name}.md").read_text(encoding="utf-8")
    for name in ALL_SCENARIOS
)


# ── Запуск ───────────────────────────────────────────────────────────────────

def _export_test_cases_md(test_cases: list[dict], path: Path) -> None:
    """Генерирует читаемый Markdown-отчёт по тест-кейсам."""
    by_tech: dict[str, list[dict]] = {}
    for tc in test_cases:
        tech = tc.get("technique", "unknown")
        by_tech.setdefault(tech, []).append(tc)

    lines = ["# Test Cases\n"]
    TECH_ORDER = ["happy_path", "boundary", "negative", "equivalence", "state_based"]
    for tech in TECH_ORDER + [t for t in by_tech if t not in TECH_ORDER]:
        cases = by_tech.get(tech, [])
        if not cases:
            continue
        lines.append(f"\n## {tech.replace('_', ' ').title()} ({len(cases)})\n")
        for tc in cases:
            exp = tc.get("expected_status", "?")
            step = tc.get("target_step", "?")
            setup_n = len(tc.get("setup_chain", []))
            lines.append(f"- **{tc['title']}**")
            lines.append(f"  - target: `{step}` | expected HTTP: `{exp}` | setup steps: {setup_n}")
            assertions = tc.get("assertions", [])
            if assertions:
                lines.append(f"  - assertions: {assertions}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    graph = build_graph()

    result = graph.invoke({
        "spec_paths": SPEC_PATHS,
        "raw_scenarios": RAW_SCENARIOS,
    })

    print("\n=== TRACE ===")
    print(" -> ".join(result["trace"]))

    print(f"\n=== ENDPOINTS ({len(result['endpoints'])}) ===")
    for ep in result["endpoints"]:
        print(f"  {ep['method']:6} {ep['path']}")
        print(f"           operationId : {ep['operation_id']}")
        print(f"           required    : {ep['required_fields']}")
        print(f"           constraints : {list(ep['constraints'].keys())}")

    collection = result["collection"]
    print(f"\n=== POSTMAN COLLECTION ===")
    print(f"  Name    : {collection['info']['name']}")
    print(f"  Folders : {len(collection['item'])}")
    for folder in collection["item"]:
        n = len(folder.get("item", []))
        print(f"    • {folder['name']}  ({n} item(s))")

    export_collection(collection, OUTPUT_COLLECTION)
    print(f"\n[OK] Коллекция: {OUTPUT_COLLECTION.resolve()}")
    print("  Import in Postman: File → Import → select file")

    # ── Артефакт тест-кейсов ─────────────────────────────────────────────────
    test_cases = result.get("test_cases", [])
    if test_cases:
        tc_json_path = OUTPUT_COLLECTION.with_name("test_cases.json")
        with open(tc_json_path, "w", encoding="utf-8") as f:
            json.dump(test_cases, f, indent=2, ensure_ascii=False, default=str)

        tc_md_path = OUTPUT_COLLECTION.with_name("test_cases.md")
        _export_test_cases_md(test_cases, tc_md_path)
        print(f"[OK] Тест-кейсы JSON: {tc_json_path.resolve()}")
        print(f"[OK] Тест-кейсы MD:   {tc_md_path.resolve()}")


if __name__ == "__main__":
    main()
