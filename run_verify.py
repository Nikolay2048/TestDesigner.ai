"""Скрипт верификации: прогон графа на UC_001-003 и сохранение всех артефактов."""
import json
import sys
from pathlib import Path

# Фиксируем корень проекта относительно этого файла — работает из любого cwd
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.config import CONFIG
from src.collection_builder import export_collection
from src.graph import build_graph

SCENARIOS = [
    "UC_001_SearchAvailableCars",
    "UC_002_CreateReservationDraft",
    "UC_003_ConfirmReservation",
]

CONFIG.base_url = "http://localhost:8000"
CONFIG.env_vars = {
    "customerId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "paymentId":  "99999999-9999-9999-9999-999999999999",
}
CONFIG.collection_name = "Carsharing — UC_001-003 Test Suite"

spec_paths = [str(ROOT / f"data/scenarios/openapi_spec/{n}.yaml") for n in SCENARIOS]
raw_scenarios = "\n\n".join(
    (ROOT / f"data/scenarios/{n}.md").read_text(encoding="utf-8") for n in SCENARIOS
)

graph = build_graph()
print("=== RUNNING GRAPH ===")
result = graph.invoke({"spec_paths": spec_paths, "raw_scenarios": raw_scenarios})

out_dir = ROOT / "output"
out_dir.mkdir(exist_ok=True)

result_path = out_dir / "run_result.json"
with open(result_path, "w", encoding="utf-8") as f:
    json.dump({k: result.get(k) for k in [
        "flow_card", "stabilized_card", "test_cases",
        "exec_results", "diagnoses", "metrics", "collection",
        "validation_errors", "trace",
    ]}, f, indent=2, ensure_ascii=False, default=str)

collection_path = out_dir / "verify_collection.json"
export_collection(result["collection"], collection_path)

# Test cases artifact
import sys
sys.path.insert(0, str(ROOT))
from src.main import _export_test_cases_md
tc_md = out_dir / "verify_test_cases.md"
_export_test_cases_md(result.get("test_cases", []), tc_md)

print(f"=== SAVED: {result_path} ===")
print(f"=== SAVED: {collection_path} ===")
print(f"=== SAVED: {tc_md} ===")
