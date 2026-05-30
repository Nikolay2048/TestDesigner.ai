"""Запуск FastAPI mock-сервера доски объявлений.

Usage:
    python run_mock_bulletin.py            # порт 8001
    python run_mock_bulletin.py --port 8002
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    uvicorn.run("mock.bulletin_board_api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
