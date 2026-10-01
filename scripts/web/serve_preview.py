"""Serve W1A static files on loopback; no Agent, model, database, or write API.

Input: optional --port (default 8765). Output: static web/ HTTP preview.
Data dependencies: W0 exported JSON only; no time alignment or research execution.
"""
from __future__ import annotations

import argparse
import logging
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


class PreviewHandler(SimpleHTTPRequestHandler):
    """Read-only static preview restricted to the web directory."""

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def translate_path(self, path: str) -> str:
        """Keep resolved paths, including symlinks, inside the public web tree."""
        resolved = Path(super().translate_path(path)).resolve()
        if not resolved.is_relative_to(WEB_ROOT.resolve()):
            return str(WEB_ROOT / "__not_found__")
        return str(resolved)

    def end_headers(self) -> None:
        """Use fresh local files and a same-origin static content policy."""
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Route request diagnostics through logging."""
        logging.info(format, *args)


def main() -> None:
    """Start a loopback server without mutating research or financial artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(PreviewHandler, directory=str(WEB_ROOT)))
    logging.info("W1A static preview: http://127.0.0.1:%s", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
