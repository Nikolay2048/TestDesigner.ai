from pathlib import Path

from src.spec_parser import parse_openapi_files
from src.state import GraphState


def spec_parser(state: GraphState) -> dict:
    paths = [Path(p) for p in state.get("spec_paths", [])]
    endpoints = parse_openapi_files(paths)
    return {
        "endpoints": [e.model_dump() for e in endpoints],
        "trace": ["spec_parser"],
    }
