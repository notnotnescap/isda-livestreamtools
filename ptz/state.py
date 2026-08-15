"""
Lightweight file-based state sharing between the PTZ controller process and the Flask web app.

The controller writes JSON; the web app reads it. No threading, no IPC — just a file in the OS temp directory.
"""

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILE = Path(tempfile.gettempdir()) / "isda-ptz-state.json"


@dataclass
class PTZStatus:
    """Snapshot of the PTZ system state."""

    controller_connected: bool = False
    controller_name: str = ""
    camera_ip: str = ""
    camera_connected: bool = False
    last_action: str = ""
    last_action_time: float = 0.0
    last_raw_input: str = ""
    axes: dict[str, float] = field(default_factory=dict)
    buttons: dict[str, bool] = field(default_factory=dict)


def write_status(status: PTZStatus):
    """Write status to the shared state file (called by controller process)."""
    data = {
        "controller_connected": status.controller_connected,
        "controller_name": status.controller_name,
        "camera_ip": status.camera_ip,
        "camera_connected": status.camera_connected,
        "last_action": status.last_action,
        "last_action_time": status.last_action_time,
        "last_raw_input": status.last_raw_input,
        "axes": status.axes,
        "buttons": status.buttons,
    }
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(STATE_FILE)


def read_status() -> PTZStatus:
    """Read the latest status from the shared state file (called by Flask)."""
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return PTZStatus()

    return PTZStatus(
        controller_connected=data.get("controller_connected", False),
        controller_name=data.get("controller_name", ""),
        camera_ip=data.get("camera_ip", ""),
        camera_connected=data.get("camera_connected", False),
        last_action=data.get("last_action", ""),
        last_action_time=data.get("last_action_time", 0.0),
        last_raw_input=data.get("last_raw_input", ""),
        axes=data.get("axes", {}),
        buttons=data.get("buttons", {}),
    )
