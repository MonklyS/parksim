"""Run the ParkSim server:  parksim-server --port 8000"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .app import create_app

    parser = argparse.ArgumentParser(prog="parksim-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db", default=None, help="SQLite file (default: $PARKSIM_DB or parksim.db)"
    )
    args = parser.parse_args(argv)
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
