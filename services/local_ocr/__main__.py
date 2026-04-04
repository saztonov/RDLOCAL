"""Entry point: python -m services.local_ocr [--port 18100]."""
from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Local OCR Service")
    parser.add_argument("--port", type=int, default=18100, help="HTTP port (default: 18100)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "services.local_ocr.server:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
