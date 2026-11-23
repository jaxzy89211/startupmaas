"""Take the TEAM_GUIDE.md screenshots with headless Google Chrome (no extra Python packages).

    streamlit run app.py --server.port 8599 --server.headless true &
    python tools/screenshots.py [--port 8599]

Drives Chrome through the DevTools protocol (a minimal WebSocket client below), waits until each page shows a
given text, and saves a PNG to docs/img/.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"

INVOICE = "2_eneco_annual_en.pdf"  # a FAKE document from tests/fake_invoices.py, written to a temp folder
SHOTS = [  # (file, url query, text that must be on the page at the end, page height, steps before that)
    ("01_dashboard_en.png", "", "Recommended share to fix", 2400, []),
    ("02_dashboard_nl.png", "?lang=nl", "Advies: deel vastzetten", 2400, []),
    ("03_interview_nl.png", "?lang=nl&mode=interview", "Stap 5", 3000, []),
    ("04_interview_contracts_nl.png", "?lang=nl&mode=interview&contracts=example", "U betaalde", 4200, []),
    (
        "05_interview_pdf_nl.png",
        "?lang=nl&mode=interview",
        "Stap 5",
        3400,
        [
            ("wait", "Stap 2"),
            ("click", "Facturen of contracten (PDF)"),
            ("wait", "meerdere tegelijk"),
            ("upload", INVOICE),
            ("wait", "Bevestig deze getallen"),
            ("click", "Bevestig deze getallen"),
            ("wait", "Bevestigd"),
        ],
    ),
]


def _wait(ws: WS, text: str, timeout: float = 240) -> bool:
    """Wait until the page shows `text` and Streamlit has finished running."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        txt = ws.call("Runtime.evaluate", expression="document.body.innerText", returnByValue=True)
        txt = txt.get("result", {}).get("value", "") or ""
        running = ws.call(
            "Runtime.evaluate",
            expression="!!document.querySelector('[data-testid=stStatusWidget]')",
            returnByValue=True,
        )["result"]["value"]
        if text in txt and not running:
            return True
        time.sleep(1)
    return False


class WS:
    """Just enough of RFC 6455 for the DevTools protocol (text frames, client masking)."""

    def __init__(self, url: str) -> None:
        host, path = url.removeprefix("ws://").split("/", 1)
        h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(
            f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        self.n = 0

    def _recv_exact(self, n: int) -> bytes:
        b = b""
        while len(b) < n:
            b += self.s.recv(n - len(b))
        return b

    def call(self, method: str, **params: object) -> dict:
        """Send one DevTools command and return its result."""
        self.n += 1
        data = json.dumps({"id": self.n, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (
            bytes([0x80 | n])
            if n < 126
            else bytes([0x80 | 126]) + struct.pack(">H", n)
            if n < 65536
            else bytes([0x80 | 127]) + struct.pack(">Q", n)
        )
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            b1, b2 = self._recv_exact(2)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            msg = json.loads(self._recv_exact(ln))
            if msg.get("id") == self.n:
                return msg.get("result", {})


def main() -> None:
    """Start Chrome, take every screenshot in SHOTS, stop Chrome."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8599, help="port of the running streamlit app")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prof = tempfile.mkdtemp()
    chrome = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--remote-debugging-port=9333",
            f"--user-data-dir={prof}",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen("http://localhost:9333/json/version", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        invoices = tempfile.mkdtemp()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
        import fake_invoices

        fake = fake_invoices.make_all(Path(invoices))
        for name, query, wait_for, height, steps in SHOTS:
            req = urllib.request.Request("http://localhost:9333/json/new?about:blank", method="PUT")
            tab = json.loads(urllib.request.urlopen(req).read())
            ws = WS(tab["webSocketDebuggerUrl"])
            ws.call("Emulation.setDeviceMetricsOverride", width=1440, height=height, deviceScaleFactor=1, mobile=False)
            ws.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
            ws.call("Page.navigate", url=f"http://localhost:{args.port}/{query}")
            t0 = time.time()
            for kind, arg in steps:
                if kind == "wait":
                    _wait(ws, arg)
                elif kind == "click":  # the innermost element showing exactly this text
                    _wait(ws, arg)
                    ws.call(
                        "Runtime.evaluate",
                        expression=(
                            "(() => { const els = [...document.querySelectorAll('label, button, p, span')]"
                            f".filter(e => e.innerText.trim() === {json.dumps(arg)});"
                            " els[els.length - 1].click(); })()"
                        ),
                    )
                    time.sleep(1)
                elif kind == "upload":
                    doc = ws.call("DOM.getDocument", depth=-1)
                    node = ws.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
                    ws.call("DOM.setFileInputFiles", files=[str(fake[arg])], nodeId=node["nodeId"])
            ok = _wait(ws, wait_for)
            time.sleep(3)  # let images finish
            png = ws.call("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
            (OUT / name).write_bytes(base64.b64decode(png))
            print(f"{name}: {'ok' if ok else 'TIMEOUT (text not found)'} ({time.time() - t0:.0f}s)")
            urllib.request.urlopen(f"http://localhost:9333/json/close/{tab['id']}")
    finally:
        chrome.terminate()
        shutil.rmtree(prof, ignore_errors=True)
        if "invoices" in locals():
            shutil.rmtree(invoices, ignore_errors=True)


if __name__ == "__main__":
    main()
