#!/usr/bin/env python3
"""
Meta Ads Dashboard — servidor local.
Lee META_TOKEN de .env e inyecta el token en el HTML antes de servirlo.
Uso: python server.py
"""
import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 3456
BASE = Path(__file__).parent


def read_token() -> str:
    env = BASE / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("META_TOKEN="):
            val = line[len("META_TOKEN="):].strip().strip('"').strip("'")
            return val
    return ""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def do_GET(self):
        if self.path in ("/", "/index.html", "/meta-ads-dashboard.html"):
            self._serve_dashboard()
        else:
            super().do_GET()

    def _serve_dashboard(self):
        html_path = BASE / "meta-ads-dashboard.html"
        if not html_path.exists():
            self.send_error(404, "meta-ads-dashboard.html no encontrado")
            return

        token = read_token()
        content = html_path.read_bytes()

        # Inyecta el token como variable global JS antes de </head>
        snippet = f"<script>window.__META_TOKEN__={json.dumps(token)};</script>".encode()
        content = content.replace(b"</head>", snippet + b"</head>", 1)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        pass  # silencia logs de cada request


if __name__ == "__main__":
    token_ok = bool(read_token())
    print(f"  Meta Ads Dashboard")
    print(f"  http://localhost:{PORT}")
    print(f"  Token: {'OK configurado' if token_ok else 'NO encontrado - edita .env'}")
    print(f"  Ctrl+C para detener\n")
    HTTPServer(("localhost", PORT), Handler).serve_forever()
