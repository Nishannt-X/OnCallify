"""Minimal stand-in for the Tomcat manager text API, for runbook tests.

GET /manager/text/list            -> "OK - Listed applications..." + "/ledgerline:running:0:ledgerline"
GET /manager/text/reload?path=/x  -> "OK - Reloaded application at context path [/x]"
Requires HTTP basic auth matching the password file given on the command line.
Usage: python3 fake_manager.py <port> <password-file>
"""

from __future__ import annotations

import base64
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1])
PASSWORD = Path(sys.argv[2]).read_text().strip()
RELOADS = Path(sys.argv[2]).with_suffix(".reloads")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quiet
        pass

    def _reply(self, status: int, text: str) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        expected = "Basic " + base64.b64encode(f"oncallify-runbook:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            self._reply(401, "unauthorized")
            return
        url = urlparse(self.path)
        if url.path == "/manager/text/list":
            self._reply(200, "OK - Listed applications for virtual host [localhost]\n/ledgerline:running:0:ledgerline\n")
        elif url.path == "/manager/text/reload":
            path = parse_qs(url.query).get("path", [""])[0]
            with RELOADS.open("a") as fh:
                fh.write(path + "\n")
            self._reply(200, f"OK - Reloaded application at context path [{path}]\n")
        else:
            self._reply(404, "FAIL - Unknown command")


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
