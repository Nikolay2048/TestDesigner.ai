"""Быстрая инспекция FlowCard от LLM — запускать вручную."""
import json
from pathlib import Path
from src.graph import build_graph
from src.main import SPEC_PATHS, RAW_SCENARIOS

graph = build_graph()
result = graph.invoke({"spec_paths": SPEC_PATHS, "raw_scenarios": RAW_SCENARIOS})
flow = result["flow_card"]

print(json.dumps(flow, indent=2, ensure_ascii=False, default=str))
