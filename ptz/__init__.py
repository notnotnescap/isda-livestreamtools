"""PTZ module — camera control utilities and state sharing.

The actual gamepad → camera control loop lives in ptz_controller.py
(a standalone process). This module provides:
- config: camera settings, mappings, sensitivity tables
- camera: VISCA-over-IP wrapper and axis mapping math
- gamepad: pygame game controller reader
- state: file-based state sharing between controller and web UI
"""

from . import config