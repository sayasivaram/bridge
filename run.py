"""Start Closer locally: python run.py  (then open the address it prints)

Picks port 8000, or the next free one if something else (say, another app) is already using it.
Use --port to choose one. Works from any folder.
"""

import argparse
import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))  # so `python closer/run.py` works from the folder above, too
os.chdir(REPO)

import uvicorn  # noqa: E402

from app.config import MissingAPIKeyError, load_settings  # noqa: E402
from app.db import SchemaOutdatedError  # noqa: E402
from app.seed import seed  # noqa: E402


def free_port(start: int, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    sys.exit(f"\nPorts {start}-{start + tries - 1} are all in use. Try: python run.py --port 9000\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start Closer.")
    parser.add_argument("--port", type=int, default=8000, help="port to use (default 8000, or the next free one)")
    args = parser.parse_args()

    settings = load_settings()
    try:
        settings.require_api_key()
    except MissingAPIKeyError as err:
        sys.exit(f"\n{err}\n")
    try:
        seed()  # create and fill data/closer.db on first run (no-op afterwards)
    except SchemaOutdatedError as err:
        sys.exit(f"\n{err}\n")

    port = free_port(args.port)
    print(f"Using {settings.provider} key from {settings.key_source}, model {settings.model}.")
    if port != args.port:
        print(f"Port {args.port} is busy, so Closer is using {port} instead.")
    print(f"Closer is running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
