"""
Central configuration loader.

All configuration for the web app, the OBS connection and the PTZ controller
lives in one place: ``config.yaml`` (see ``config.example.yaml`` for a
template).  ``config.yaml`` is gitignored and never committed.

Both the Flask app (``app.py``) and the PTZ controller process
(``ptz_controller.py`` → ``ptz.config``) read their settings from here.
"""

import os
from pathlib import Path

import yaml

CONFIG_FILE = Path(
    os.environ.get("ISDA_CONFIG_FILE", Path(__file__).resolve().parent / "config.yaml"),
)

# Sensible defaults so the app still boots with a missing/partial config file.
_DEFAULTS: dict = {
    "obs": {"ip": "127.0.0.1", "port": 4455, "password": ""},
    "ptz": {
        "cameras": [],
        "osee_host": "127.0.0.1",
        "osee_port": 19010,
        "osee_cut_transition": "",
        "osee_auto_transition": "",
        "sensitivity": {
            "pan": {"joy": [0, 0.02, 0.1, 0.3, 0.4, 0.5], "cam": [0, 0, 1, 4, 7, 10]},
            "tilt": {"joy": [0, 0.04, 0.1, 0.3, 0.4, 0.5], "cam": [0, 0, 1, 3, 7, 9]},
            "zoom": {"joy": [0, 0.05, 0.5], "cam": [0, 0, 3]},
        },
        "invert_pan": False,
        "invert_tilt": False,
        "invert_zoom": True,
        "controls": {
            "axes": {},
            "buttons": {},
            "buttons_hold": {},
            "hats": {},
        },
        "poll_interval": 0.03,
        "reconnect_interval": 5,
        "long_press_time": 1.0,
    },
    "app": {"host": "0.0.0.0", "port": 5000, "debug": False},  # noqa: S104 — LAN-only default
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` (override wins)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load() -> dict:
    """Load configuration from ``config.yaml`` (missing file → defaults)."""
    if not CONFIG_FILE.exists():
        return _DEFAULTS
    with CONFIG_FILE.open() as fh:
        user_cfg = yaml.safe_load(fh) or {}
    return _deep_merge(_DEFAULTS, user_cfg)


_cfg = load()

# --- OBS --------------------------------------------------------------------
OBS_IP = _cfg["obs"]["ip"]
OBS_PORT = int(_cfg["obs"]["port"])
OBS_PASSWORD = _cfg["obs"]["password"]

# --- PTZ --------------------------------------------------------------------
CAMERAS: list[str] = _cfg["ptz"]["cameras"]
OSEE_HOST = _cfg["ptz"]["osee_host"]
OSEE_PORT = int(_cfg["ptz"]["osee_port"])
OSEE_CUT_TRANSITION = _cfg["ptz"]["osee_cut_transition"]
OSEE_AUTO_TRANSITION = _cfg["ptz"]["osee_auto_transition"]
SENSITIVITY = _cfg["ptz"]["sensitivity"]
INVERT_PAN = _cfg["ptz"]["invert_pan"]
INVERT_TILT = _cfg["ptz"]["invert_tilt"]
INVERT_ZOOM = _cfg["ptz"]["invert_zoom"]
CONTROLS = _cfg["ptz"]["controls"]
POLL_INTERVAL = float(_cfg["ptz"]["poll_interval"])
RECONNECT_INTERVAL = float(_cfg["ptz"]["reconnect_interval"])
LONG_PRESS_TIME = float(_cfg["ptz"]["long_press_time"])

# --- Flask app ----------------------------------------------------------------
APP_HOST = _cfg["app"]["host"]
APP_PORT = int(_cfg["app"]["port"])
APP_DEBUG = bool(_cfg["app"]["debug"])

# Dict form for template injection (e.g. OBS settings for the web UI).
OBS_CONFIG = {"ip": OBS_IP, "port": OBS_PORT, "password": OBS_PASSWORD}
