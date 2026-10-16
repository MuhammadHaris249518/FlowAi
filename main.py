"""Deployment smoke test for Alternate Clouds.

Checks three things before we build the real project:
  1. The container starts and the public URL opens   -> GET /
  2. The platform health check works                  -> GET /health
  3. WebSockets pass through the platform's proxy     -> WS  /ws (echo)
"""

import os
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="Agentika deploy test")

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Deploy test</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 16px">
  <h1>Deployment works</h1>
  <p>Running on Alternate Clouds. WebSocket test:</p>
  <p id="ws">connecting...</p>
  <script>
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws`);
    const out = document.getElementById("ws");
    sock.onopen = () => sock.send("ping");
    sock.onmessage = (e) => { out.textContent = "OK - server replied: " + e.data; };
    sock.onerror = () => { out.textContent = "FAILED - WebSockets blocked, use HTTP polling"; };
  </script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "port": os.environ.get("PORT", "8000"),
    }


@app.websocket("/ws")
async def ws_echo(sock: WebSocket) -> None:
    await sock.accept()
    try:
        while True:
            msg = await sock.receive_text()
            await sock.send_text(f"echo: {msg}")
    except WebSocketDisconnect:
        pass
