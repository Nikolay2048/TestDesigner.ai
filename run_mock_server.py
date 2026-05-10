#!/usr/bin/env python3
"""
Entry point for the Carsharing Mock Server.

Usage:
    python run_mock_server.py [--host HOST] [--port PORT] [--debug]

Default: http://127.0.0.1:8080

Logs are written to: logs/mock_server.log
Seed IDs reference:  GET /mock/seed-ids
Full state dump:     GET /mock/state
Reset to seed:       POST /mock/reset
"""

import argparse
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(__file__))

from mock_server.app import create_app
from mock_server.logger_config import get_logger

log = get_logger()


def parse_args():
    p = argparse.ArgumentParser(description="Carsharing Mock Server")
    p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    p.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return p.parse_args()


def main():
    args = parse_args()
    app = create_app()

    log.info("=" * 60)
    log.info("  Carsharing Mock Server")
    log.info("  http://%s:%d", args.host, args.port)
    log.info("  Seed IDs:  http://%s:%d/mock/seed-ids", args.host, args.port)
    log.info("  State:     http://%s:%d/mock/state", args.host, args.port)
    log.info("  Health:    http://%s:%d/health", args.host, args.port)
    log.info("  Logs:      logs/mock_server.log")
    log.info("=" * 60)

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
