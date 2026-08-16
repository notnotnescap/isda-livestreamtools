"""
VISCA-over-IP camera wrapper.

Provides a simple interface for PTZ camera operations,
including joystick-to-speed conversion.
"""

import time

import visca_over_ip
from numpy import interp
from visca_over_ip import Camera
from visca_over_ip.exceptions import ViscaException

from . import config


class PTZCamera:
    """Wrapper around a VISCA-over-IP camera with sensitivity curves."""

    def __init__(self, ip: str):
        self.ip = ip
        self._cam: Camera | None = None
        self._connect()

    def _connect(self) -> bool:
        """Connect to the camera. Returns True on success."""
        try:
            self._cam = Camera(self.ip)
        except (visca_over_ip.exceptions.NoQueryResponse, OSError) as exc:
            print(f"PTZCamera: could not connect to {self.ip}: {exc}")
            self._cam = None
            return False
        return True

    @property
    def connected(self) -> bool:
        return self._cam is not None

    def reconnect(self) -> bool:
        """Attempt reconnection. Returns True if now connected."""
        if self.connected:
            return True
        return self._connect()

    # -- Movement -----------------------------------------------------------

    def pantilt(self, pan: int, tilt: int):
        """Set pan/tilt speed (-10 to 10). 0 = stop."""
        if self._cam:
            self._cam.pantilt(pan, tilt)

    def zoom(self, speed: int):
        """Set zoom speed (-3 to 3). 0 = stop."""
        if self._cam:
            self._cam.zoom(speed)

    def stop(self):
        """Stop all movement."""
        if self._cam:
            self._cam.pantilt(0, 0)
            self._cam.zoom(0)

    # -- Presets ------------------------------------------------------------

    def recall_preset(self, index: int):
        """Recall a saved preset (0 = home)."""
        if self._cam:
            self._cam.recall_preset(index)

    def save_preset(self, index: int):
        """Save current position as a preset."""
        if self._cam:
            self._cam.save_preset(index)

    # -- Exposure -----------------------------------------------------------

    def increase_gain(self):
        """Increase exposure/gain by one step."""
        if self._cam:
            self._cam.increase_gain()

    def decrease_gain(self):
        """Decrease exposure/gain by one step."""
        if self._cam:
            self._cam.decrease_gain()

    # -- Focus --------------------------------------------------------------

    def set_focus_mode(self, mode: str):
        """Set focus mode: 'auto' or 'manual'."""
        if self._cam:
            self._cam.set_focus_mode(mode)

    def manual_focus(self, direction: int):
        """Step focus near (-1) or far (1)."""
        if self._cam:
            self._cam.manual_focus(direction)
            time.sleep(0.1)
            self._cam.manual_focus(0)

    # -- Zoom compensation --------------------------------------------------

    def zoom_sensitivity_multiplier(self) -> float:
        """Reduce sensitivity as zoom increases (prevents jerky movement)."""
        if not self._cam:
            return 1.0
        try:
            zoom_pos = self._cam.get_zoom_position()  # 0..16384
            return 1.0 - (0.8 * zoom_pos / 16384)
        except ViscaException:
            return 1.0


class AxisMapper:
    """Converts raw joystick axis values to camera speed using config tables."""

    def __init__(self):
        self._tables = config.SENSITIVITY

    def to_speed(
        self,
        raw: float,
        table_name: str,
        *,
        invert: bool = False,
        speedup: bool = False,
        multiplier: float = 1.0,
    ) -> int:
        """
        Convert a joystick axis value (-1..1) to a camera speed integer.

        Args:
            raw: Raw axis value from the controller (-1 to 1).
            table_name: Key in SENSITIVITY ('pan', 'tilt', 'zoom').
            invert: Flip the output sign.
            speedup: Double the speed (for quick-pan/quick-zoom buttons).
            multiplier: Additional scaling factor (e.g. zoom compensation).

        """
        table = self._tables[table_name]
        sign = 1 if raw >= 0 else -1
        if invert:
            sign *= -1
        if speedup:
            sign *= 2
        speed = interp(abs(raw), table["joy"], table["cam"])
        return sign * round(speed * multiplier)


# -- Utility for combining multiple pan inputs -------------------------------


def normalize_trigger(raw: float) -> float:
    """Map a trigger axis from -1..1 to 0..1."""
    return (raw + 1) / 2


def combine_pan(pan_left: float, pan_right: float, pan_stick: float) -> float:
    """Combine left trigger, right trigger, and joystick into one pan value."""
    # Right trigger pans right (positive), left trigger pans left (negative).
    combined = normalize_trigger(pan_right) - normalize_trigger(pan_left) + pan_stick
    return max(-1.0, min(1.0, combined))
