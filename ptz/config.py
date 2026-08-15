"""PTZ configuration — values now live in the root config.yaml.

``ptz_controller.py`` and friends can still ``from ptz import config``
and use ``config.CAMERAS`` etc.  The actual values come from the root
:mod:`config` module, which loads ``config.yaml``.
"""

from pathlib import Path
import sys

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import (  # noqa: F401, E402
    CAMERAS,
    CONTROLS,
    INVERT_PAN,
    INVERT_TILT,
    INVERT_ZOOM,
    LONG_PRESS_TIME,
    OSEE_AUTO_TRANSITION,
    OSEE_CUT_TRANSITION,
    OSEE_HOST,
    OSEE_PORT,
    POLL_INTERVAL,
    RECONNECT_INTERVAL,
    SENSITIVITY,
)