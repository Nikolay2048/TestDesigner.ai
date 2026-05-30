"""Запуск FastAPI mock-сервера каршеринга.

Usage:
    python run_mock.py            # порт 8000
    python run_mock.py --port 8080
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run("mock.api:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    main()
