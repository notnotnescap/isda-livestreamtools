"""ISDA Live Stream Tools - Flask Backend

Hosts the main control UI and a dynamic transparent overlay page.
Uses SSE (Server-Sent Events) for real-time overlay triggers.
"""

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
    """Queue an overlay animation on ALL connected overlay clients.

    JSON body: {"text": "Welcome", "duration": 8}
    """
    data = request.get_json(silent=True) or {}
    event = {
        "text": str(data.get("text", "Welcome")),
        "duration": max(8, int(data.get("duration", 8))),
    }
    # Broadcast to every connected overlay page
    for q in _overlay_clients:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="ISDA Live Stream Tools")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", config.APP_PORT)),
        help=f"Port to listen on (default: {config.APP_PORT}, or $PORT)"
    )
    args = parser.parse_args()

    port = args.port
    print("ISDA Live Stream Tools")
    print(f"   Main UI:  http://localhost:{port}/")
    print(f"   Overlay:  http://localhost:{port}/overlay  (add as OBS browser source)")
    print(f"   PTZ controller:  uv run python ptz_controller.py  (optional, separate terminal)")
    app.run(host=config.APP_HOST, port=port, debug=config.APP_DEBUG, threaded=True)