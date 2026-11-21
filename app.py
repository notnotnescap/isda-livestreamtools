"""
ISDA Live Stream Tools - Flask Backend

Hosts the main control UI and a dynamic transparent overlay page.
Uses SSE (Server-Sent Events) for real-time overlay triggers.
"""

import contextlib
import json
import queue

from flask import Flask, Response, jsonify, render_template, request

import config
from ptz.state import read_status

app = Flask(__name__)

# Fan-out list: one queue per connected overlay client.
# When a trigger comes in, the event is pushed to every queue.
_overlay_clients: list[queue.Queue[dict]] = []


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Main control UI."""
    return render_template(
        "index.html",
        host_url=request.host_url.rstrip("/"),
        obs_config=config.OBS_CONFIG,
    )


@app.route("/overlay")
def overlay():
    """Transparent overlay page for OBS browser source."""
    return render_template("overlay.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/trigger-overlay", methods=["POST"])
def trigger_overlay():
    """
    Queue an overlay animation on ALL connected overlay clients.

    JSON body: {"text": "Welcome", "duration": 8}
    """
    data = request.get_json(silent=True) or {}
    event = {
        "text": str(data.get("text", "Welcome")),
        "duration": max(8, int(data.get("duration", 8))),
    }
    # Broadcast to every connected overlay page
    for q in _overlay_clients:
        with contextlib.suppress(queue.Full):
            q.put_nowait(event)
    return jsonify({"status": "ok", "clients": len(_overlay_clients)})


@app.route("/api/overlay-events")
def overlay_events_stream():
    """SSE endpoint: each overlay page gets its own queue."""
    q: queue.Queue[dict] = queue.Queue()
    _overlay_clients.append(q)

    def event_stream():
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            _overlay_clients.remove(q)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# PTZ
# ---------------------------------------------------------------------------

@app.route("/api/ptz/status")
def ptz_status():
    """Current PTZ system state: controller, camera, axes, last action."""
    s = read_status()
    return jsonify({
        "controller_connected": s.controller_connected,
        "controller_name": s.controller_name,
        "camera_ip": s.camera_ip,
        "camera_connected": s.camera_connected,
        "last_action": s.last_action,
        "last_raw_input": s.last_raw_input,
        "axes": s.axes,
        "buttons": s.buttons,
    })


# ---------------------------------------------------------------------------
# Config reload
# ---------------------------------------------------------------------------

@app.route("/api/reload-config", methods=["POST"])
def reload_config():
    """Reload config.yaml without restarting the server."""
    import importlib
    import sys

    # Remove all cached config modules so they re-import on next access
    for mod_name in list(sys.modules):
        if mod_name.startswith("config") or mod_name == "config":
            del sys.modules[mod_name]
    # Also flush the ptz.config reference which caches config values
    if "ptz.config" in sys.modules:
        del sys.modules["ptz.config"]

    import config as cfg
    importlib.reload(cfg)

    # Reload ptz.config which re-exports from the main config
    from ptz import config as ptz_cfg
    importlib.reload(ptz_cfg)

    return jsonify({"status": "ok", "message": "Configuration reloaded"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _spawn_ptz_controller() -> None:
    """Start the PTZ controller as a background subprocess.

    Uses the same Python interpreter that's running this Flask app.
    Silently skips if the process is already running.
    """
    import subprocess
    import sys

    # Check if ptz_controller is already running (avoid duplicates)
    try:
        import psutil  # type: ignore[import-untyped]
        this_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if proc.info["pid"] == this_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            if len(cmdline) >= 2 and "ptz_controller.py" in cmdline[-1]:
                print("   PTZ controller: already running, skipping")
                return
    except ImportError:
        pass  # psutil not available — just try to start

    python_exe = sys.executable
    script = os.path.join(os.path.dirname(__file__), "ptz_controller.py")
    try:
        proc = subprocess.Popen(
            [python_exe, script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        print(f"   PTZ controller: started (pid {proc.pid})")
    except Exception as exc:
        print(f"   PTZ controller: failed to start — {exc}")


if __name__ == "__main__":
    import argparse
    import os

    # Auto-start the PTZ controller in the background
    _spawn_ptz_controller()

    parser = argparse.ArgumentParser(description="ISDA Live Stream Tools")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", config.APP_PORT)),
        help=f"Port to listen on (default: {config.APP_PORT}, or $PORT)",
    )
    args = parser.parse_args()

    port = args.port
    print("ISDA Live Stream Tools")
    print(f"   Main UI:  http://localhost:{port}/")
    print(f"   Overlay:  http://localhost:{port}/overlay  (add as OBS browser source)")
    app.run(host=config.APP_HOST, port=port, debug=config.APP_DEBUG, threaded=True)
