"""Run with:  python -m wcl_bot.web   [--host 0.0.0.0 --port 8000 --reload]"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true",
                   help="Auto-reload on code changes (dev only).")
    args = p.parse_args()

    uvicorn.run(
        "wcl_bot.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
