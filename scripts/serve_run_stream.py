#!/usr/bin/env python3
"""Serve the newest run's turn stream, and expose it through a tunnel.

Renders on every request rather than writing snapshots, so the page is only ever
as stale as the run bundle on disk - which the run appends to as each turn ends.
The page polls `fragment` every few seconds, so a turn shows up on a phone a
moment after it happens.

The tunnel is a Cloudflare quick tunnel: a random public hostname, no auth. What
is exposed is one Kenshi run's plans and refusals, which is not sensitive, but it
is genuinely public while it runs - so the tunnel dies with this process.

Usage: python scripts/serve_run_stream.py [--port 8765] [--no-tunnel]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_run_stream import (  # noqa: E402
    _latest_run_id,
    _read,
    render_document,
    render_fragment,
)

CLOUDFLARED = Path.home() / ".local" / "bin" / "cloudflared"
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class _Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        try:
            run = _read(_latest_run_id())
        except Exception as exc:  # a run that has not written events yet
            self._send(f"<p>waiting for a run: {exc}</p>", "text/html; charset=utf-8")
            return
        if self.path.rstrip("/").endswith("fragment"):
            self._send(render_fragment(run), "text/html; charset=utf-8")
            return
        self._send(render_document(run), "text/html; charset=utf-8")

    def log_message(self, *args: object) -> None:
        """Quiet: this runs beside a live run and must not fight for the console."""


def _open_tunnel(port: int, timeout: float = 25.0) -> tuple[subprocess.Popen[str] | None, str]:
    if not CLOUDFLARED.is_file():
        return None, ""
    process = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + timeout
    assert process.stdout is not None
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        found = _URL.search(line)
        if found:
            threading.Thread(target=_drain, args=(process,), daemon=True).start()
            return process, found.group(0)
    return process, ""


def _drain(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for _ in process.stdout:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-tunnel", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tunnel = None
    if args.no_tunnel:
        print(f"run stream: http://127.0.0.1:{args.port}", flush=True)
    else:
        tunnel, url = _open_tunnel(args.port)
        if url:
            print(f"run stream: {url}", flush=True)
        elif tunnel is None:
            print(
                f"run stream: http://127.0.0.1:{args.port} "
                "(cloudflared not found; local only)",
                flush=True,
            )
        else:
            print(
                f"run stream: http://127.0.0.1:{args.port} "
                "(tunnel did not report a hostname; local only)",
                flush=True,
            )

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        if tunnel is not None:
            tunnel.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
