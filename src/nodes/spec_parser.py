from pathlib import Path

from src.logger import get_logger
from src.spec_parser import parse_openapi_files
from src.state import GraphState

log = get_logger("spec_parser")


def spec_parser(state: GraphState) -> dict:
    paths = [Path(p) for p in state.get("spec_paths", [])]
    log.info("Parsing %d spec file(s): %s", len(paths), [p.name for p in paths])
    endpoints = parse_openapi_files(paths)
    log.info("Parsed %d endpoint(s):", len(endpoints))
    for ep in endpoints:
        log.debug("  %s %s [%s] required=%s constraints=%s",
                  ep.method, ep.path, ep.operation_id,
                  ep.required_fields, list(ep.constraints.keys()))
    return {
        "endpoints": [e.model_dump() for e in endpoints],
        "trace": ["spec_parser"],
    }
