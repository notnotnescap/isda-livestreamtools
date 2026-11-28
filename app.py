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
# Version & update
# ---------------------------------------------------------------------------

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent


def _git_head() -> str:
    """Return the current local HEAD commit hash (short)."""
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
            )
            .stdout.strip()
        )
    except Exception:
        return "unknown"


def _git_remote_head() -> str | None:
    """Return the origin/main commit hash, or None if unreachable."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--quiet"],
            capture_output=True, cwd=_REPO_ROOT, timeout=10,
        )
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "origin/main"],
                capture_output=True, text=True, cwd=_REPO_ROOT, timeout=5,
            )
            .stdout.strip()
        )
    except Exception:
        return None


def _git_pull() -> bool:
    """Pull latest code from origin/main. Returns True on success."""
    try:
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            capture_output=True, cwd=_REPO_ROOT, timeout=30,
        )
        return True
    except Exception:
        return False


@app.route("/api/version")
def api_version():
    """Return the current local version (commit hash) and the remote version."""
    local = _git_head()
    remote = _git_remote_head()
    return jsonify({
        "local": local,
        "remote": remote or "unknown",
        "update_available": remote is not None and remote != local,
    })


@app.route("/api/update", methods=["POST"])
def api_update():
    """Pull latest code from GitHub. Returns new version info."""
    old = _git_head()
    ok = _git_pull()
    new = _git_head()
    return jsonify({
        "status": "ok" if ok else "error",
        "old_version": old,
        "new_version": new,
        "updated": old != new,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import os

    print("   PTZ controller: started separately via Task Scheduler")

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
