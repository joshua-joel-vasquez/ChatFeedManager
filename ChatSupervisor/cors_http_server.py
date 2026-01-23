"""A tiny static file server with permissive CORS headers.

Why this exists
--------------
OBS Browser Source is frequently configured with **Local File** sources
(`file://...`). Modern Chromium blocks `file://` pages from fetching
`http://127.0.0.1:PORT/...` unless the server explicitly allows it via CORS.

The stock `python -m http.server` does *not* send CORS headers, which causes
overlay pages to show `Failed to fetch` even though the server is running.

This file server is still intentionally tiny and dependency-free.
"""

from __future__ import annotations

import argparse
import http.server
import socket
import socketserver


class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
    # Be quiet by default (OBS can be noisy). Toggle with --verbose.
    verbose: bool = False

    def log_message(self, format: str, *args) -> None:  # noqa: A003 (format)
        if self.verbose:
            super().log_message(format, *args)

    def end_headers(self) -> None:
        # Allow overlay pages opened from file:// (Origin: null) to fetch.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

        # Avoid caching in OBS while you're iterating.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()


def main() -> int:
    ap = argparse.ArgumentParser(description="Static file server with CORS headers.")
    ap.add_argument("port", type=int, help="Port to listen on")
    # Default to a dual-stack IPv6 bind (accepts both IPv6 + IPv4 on most systems).
    # This avoids the common "localhost -> ::1" issue where an IPv4-only server
    # appears to be "down".
    ap.add_argument("--bind", default="::", help="Bind address (default: ::, dual-stack if available)")
    ap.add_argument("--verbose", action="store_true", help="Print HTTP logs")
    args = ap.parse_args()

    # Threaded server so simultaneous requests don't block.
    handler = CORSRequestHandler
    handler.verbose = bool(args.verbose)

    def _make_server(bind: str):
        bind = str(bind)
        port = int(args.port)

        # If it looks like an IPv6 address, use an IPv6 socket and try to enable
        # dual-stack (IPv4-mapped) mode.
        if ":" in bind:
            class _V6Server(socketserver.ThreadingTCPServer):
                address_family = socket.AF_INET6
                allow_reuse_address = True

                def server_bind(self):
                    try:
                        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                    except Exception:
                        pass
                    return super().server_bind()

            return _V6Server((bind, port), handler)

        class _V4Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        return _V4Server((bind, port), handler)

    try:
        httpd = _make_server(args.bind)
    except OSError:
        # Fallback for systems without IPv6 enabled.
        httpd = _make_server("127.0.0.1")

    with httpd:
        httpd.daemon_threads = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
