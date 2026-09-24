#!/usr/bin/env python3
"""Convierte un reporte HTML en un PDF de UNA sola pagina (sin cortes).

Maneja Chrome por CDP: carga el HTML, espera fuentes + charts, mide el alto real
del contenido y genera un PDF con una pagina a medida (ancho fijo, alto = contenido).
Asi los graficos quedan a tamaño completo y el reporte no se parte en hojas.

Uso:
  python html_to_pdf.py <input.html> [output.pdf] [--width-in 11]

Requiere Chrome/Edge instalado y el paquete websocket-client.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import websocket  # websocket-client

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit("ERROR: no se encontro Chrome ni Edge.")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CDP:
    """Cliente CDP minimo sobre una sesion de target (flatten)."""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, max_size=None)
        self._id = 0
        self.session_id = None

    def send(self, method, params=None, timeout=60):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if self.session_id:
            msg["sessionId"] = self.session_id
        self.ws.send(json.dumps(msg))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    def wait_event(self, method, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.ws.settimeout(max(0.1, end - time.time()))
            try:
                data = json.loads(self.ws.recv())
            except Exception:
                break
            if data.get("method") == method:
                return data.get("params", {})
        return None


def render(html_path: Path, pdf_path: Path, width_in: float):
    chrome = find_chrome()
    port = free_port()
    profile = Path.home() / ".cache" / "reporte_pdf_profile"
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "--remote-allow-origins=*", "--hide-scrollbars",
         "--force-device-scale-factor=1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        import urllib.request
        ver = None
        for _ in range(100):  # esperar a que el devtools levante
            try:
                ver = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1).read())
                break
            except Exception:
                time.sleep(0.1)
        if not ver:
            sys.exit("ERROR: Chrome no expuso el endpoint de DevTools.")

        browser = CDP(ver["webSocketDebuggerUrl"])
        target = browser.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        att = browser.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        browser.session_id = att["sessionId"]

        browser.send("Page.enable")
        browser.send("Emulation.setEmulatedMedia", {"media": "print"})
        # Ancho de layout = ancho de pagina (a 96dpi). Alto provisional grande.
        width_px = round(width_in * 96)
        browser.send("Emulation.setDeviceMetricsOverride", {
            "width": width_px, "height": 1200, "deviceScaleFactor": 1, "mobile": False})

        uri = html_path.resolve().as_uri()
        browser.send("Page.navigate", {"url": uri})
        browser.wait_event("Page.loadEventFired", timeout=30)

        # Esperar fuentes + un par de frames para que Chart.js termine de dibujar.
        browser.send("Runtime.evaluate", {
            "expression": "document.fonts.ready.then(()=>new Promise(r=>setTimeout(r,1800)))",
            "awaitPromise": True}, timeout=30)

        h = browser.send("Runtime.evaluate", {
            "expression": "Math.ceil(Math.max("
                          "document.documentElement.scrollHeight,"
                          "document.body.scrollHeight,"
                          "document.body.getBoundingClientRect().bottom))",
            "returnByValue": True})["result"]["value"]
        # +0.08\" evita que un redondeo sub-pixel derrame a una 2da pagina.
        height_in = h / 96.0 + 0.08

        pdf = browser.send("Page.printToPDF", {
            "printBackground": True,
            "preferCSSPageSize": False,
            "paperWidth": width_in,
            "paperHeight": height_in,
            "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
        }, timeout=120)

        import base64
        pdf_path.write_bytes(base64.b64decode(pdf["data"]))
        print(f"  OK -> {pdf_path}  ({width_in:.1f} x {height_in:.1f} in, 1 pagina)")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="HTML de entrada")
    ap.add_argument("output", nargs="?", default=None, help="PDF de salida")
    ap.add_argument("--width-in", type=float, default=11.0,
                    help="Ancho de pagina en pulgadas (default 11, ancho tabloid)")
    args = ap.parse_args()
    inp = Path(args.input)
    out = Path(args.output) if args.output else inp.with_suffix(".pdf")
    render(inp, out, args.width_in)


if __name__ == "__main__":
    main()
