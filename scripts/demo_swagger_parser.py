"""
Demo script: run the SwaggerParser on the carsharing OpenAPI spec and print
a human-readable summary of each endpoint + its example request.

Usage:
    python scripts/demo_swagger_parser.py
"""

import io
import json
import sys
from pathlib import Path

# Force UTF-8 output on Windows so box-drawing / Cyrillic chars print correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import get_config
from src.utils.logging_setup import setup_logging
from src.modules.swagger_parser import SwaggerParser

cfg = get_config()
setup_logging(level=cfg.logging.level, fmt=cfg.logging.format)

import logging
log = logging.getLogger(__name__)


def main() -> None:
    spec_path = Path("data/openapi.yaml")
    if not spec_path.exists():
        log.error("Spec file not found: %s", spec_path.resolve())
        sys.exit(1)

    parser = SwaggerParser(spec_path)
    parsed = parser.parse()

    print("\n" + "=" * 70)
    print(f"  {parsed.title}  v{parsed.version}")
    print(f"  Base URL: {parsed.base_url}")
    print("=" * 70)
    print()
    print(parsed.summary_table())
    print()

    for ep in parsed.endpoints:
        print("─" * 70)
        print(f"[{ep.method}] {ep.path}")
        if ep.summary:
            print(f"  Summary    : {ep.summary}")
        if ep.description:
            print(f"  Description: {ep.description}")
        if ep.operation_id:
            print(f"  OperationId: {ep.operation_id}")

        if ep.parameters:
            print("  Parameters:")
            for p in ep.parameters:
                req_mark = "*" if p.required else " "
                print(
                    f"    [{req_mark}] {p.location:<7} {p.name:<20} "
                    f"example={json.dumps(p.example)}"
                )

        if ep.request_body:
            rb = ep.request_body
            print(f"  Request body ({rb.content_type}, required={rb.required}):")
            print(f"    example: {json.dumps(rb.example, indent=6, ensure_ascii=False)}")

        print("  Responses:")
        for resp in ep.responses:
            print(f"    {resp.status_code}: {resp.description}")
            if resp.example is not None:
                print(
                    f"      example: {json.dumps(resp.example, indent=10, ensure_ascii=False)}"
                )

        print("  Example request:")
        req = ep.example_request
        print(f"    {req.method} {req.url}")
        print(f"    headers: {json.dumps(req.headers)}")
        if req.query_params:
            print(f"    query:   {json.dumps(req.query_params)}")
        if req.body is not None:
            print(f"    body:    {json.dumps(req.body, indent=10, ensure_ascii=False)}")
        print()


if __name__ == "__main__":
    main()
