"""
Game controller reader.

Wraps a physical game controller, maps buttons/axes/hats via config,
and provides an event-polling interface.

Uses pygame when available (works with most controllers via SDL).
Falls back to the ``inputs`` library (raw HID / XInput) when pygame
can't find any controllers — this handles controllers that SDL's game
controller database doesn't recognise (e.g. some 8BitDo models).
"""

import time
from dataclasses import dataclass, field
from typing import Any

import pygame

from . import config

# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------


@dataclass
class GamepadState:
    """Snapshot of the gamepad state at a point in time."""

    connected: bool = False
    name: str = ""
    axes: dict[str, float] = field(default_factory=dict)
    buttons: dict[str, bool] = field(default_factory=dict)
    hats: dict[str, tuple[int, int]] = field(default_factory=dict)
    short_presses: list[str] = field(default_factory=list)
    long_presses: list[str] = field(default_factory=list)
    short_hats: list[str] = field(default_factory=list)
    long_hats: list[str] = field(default_factory=list)
    raw_events: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pygame backend (primary)
# ---------------------------------------------------------------------------


class _PygameBackend:
    """Gamepad reader using pygame / SDL."""

    def __init__(self) -> None:
        self._joystick: Any = None
        self._down_times: dict[int, float] = {}
        self._short_presses: list[str] = []
        self._long_presses: list[str] = []
        self._short_hats: list[str] = []
        self._long_hats: list[str] = []
        self._raw_events: list[str] = []
        self._last_reconnect: float = 0.0

    def connect(self) -> bool:
        pygame.joystick.quit()
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self._joystick = None
            return False
        for idx in range(count):
            try:
                stick = pygame.joystick.Joystick(idx)
                stick.init()
            except pygame.error:
                continue
            self._joystick = stick
            return True
        self._joystick = None
        return False

    @property
    def connected(self) -> bool:
        if self._joystick is None:
            return False
        if pygame.joystick.get_count() == 0:
            self._joystick = None
            return False
        return True

    @property
    def name(self) -> str:
        if self._joystick:
            try:
                return self._joystick.get_name()
            except (pygame.error, AttributeError):
                return ""
        return ""

    def maybe_reconnect(self) -> None:
        now = time.time()
        if not self.connected and now - self._last_reconnect > config.RECONNECT_INTERVAL:
            self._last_reconnect = now
            self.connect()

    def poll(self) -> GamepadState:
        self._process_events()
        short_presses, self._short_presses = self._short_presses, []
        long_presses, self._long_presses = self._long_presses, []
        short_hats, self._short_hats = self._short_hats, []
        long_hats, self._long_hats = self._long_hats, []
        raw_events, self._raw_events = self._raw_events, []

        if not self.connected:
            return GamepadState(
                connected=False,
                short_presses=short_presses,
                long_presses=long_presses,
                short_hats=short_hats,
                long_hats=long_hats,
            )

        axes: dict[str, float] = {}
        for name, idx in config.CONTROLS["axes"].items():
            try:
                axes[name] = self._joystick.get_axis(idx)
            except (pygame.error, AttributeError):
                axes[name] = 0.0

        buttons: dict[str, bool] = {}
        for name, idx in config.CONTROLS["buttons"].items():
            try:
                buttons[name] = self._joystick.get_button(idx) == 1
            except (pygame.error, AttributeError):
                buttons[name] = False

        return GamepadState(
            connected=True,
            name=self.name,
            axes=axes,
            buttons=buttons,
            short_presses=short_presses,
            long_presses=long_presses,
            short_hats=short_hats,
            long_hats=long_hats,
            raw_events=raw_events,
        )

    def _process_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                idx = event.button
                self._down_times[idx] = time.time()
                name = config.CONTROLS["buttons"].get(str(idx)) or config.CONTROLS["buttons"].get(idx)
                label = f"Btn {idx}" if name is None else f"Btn {idx} ({name})"
                self._raw_events.append(label + " ↓")

            elif event.type == pygame.JOYBUTTONUP:
                idx = event.button
                name = config.CONTROLS["buttons"].get(str(idx)) or config.CONTROLS["buttons"].get(idx)
                label = f"Btn {idx}" if name is None else f"Btn {idx} ({name})"
                self._raw_events.append(label + " ↑")
                if idx in self._down_times:
                    duration = time.time() - self._down_times.pop(idx)
                    if name and duration > config.LONG_PRESS_TIME:
                        self._long_presses.append(name)
                    elif name:
                        self._short_presses.append(name)

            elif event.type == pygame.JOYAXISMOTION:
                idx = event.axis
                name = config.CONTROLS["axes"].get(str(idx)) or config.CONTROLS["axes"].get(idx)
                label = f"Axis {idx}" if name is None else f"Axis {idx} ({name})"
                self._raw_events.append(f"{label} = {event.value:.3f}")

            elif event.type == pygame.JOYHATMOTION:
                self._raw_events.append(f"Hat {event.hat} = {event.value}")


# ---------------------------------------------------------------------------
# Inputs backend (fallback — works when SDL can't detect the controller)
# ---------------------------------------------------------------------------


class _InputsBackend:
    """Gamepad reader using the ``inputs`` library (raw HID / XInput)."""

    def __init__(self) -> None:
        self._device: Any = None
        self._last_reconnect: float = 0.0

        # Map inputs event codes → axis indices (XInput convention)
        self._INPUTS_AXES: dict[str, int] = {
            "ABS_X": 0,  # left stick X
            "ABS_Y": 1,  # left stick Y
            "ABS_RX": 2,  # right stick X
            "ABS_RY": 3,  # right stick Y
            "ABS_Z": 4,  # left trigger
            "ABS_RZ": 5,  # right trigger
            "ABS_HAT0X": 6,  # D-pad X
            "ABS_HAT0Y": 7,  # D-pad Y
        }
        # Map inputs event codes → button indices (XInput convention)
        self._INPUTS_BUTTONS: dict[str, int] = {
            "BTN_SOUTH": 0,  # A
            "BTN_EAST": 1,  # B
            "BTN_NORTH": 3,  # Y
            "BTN_WEST": 2,  # X
            "BTN_TL": 4,  # LB
            "BTN_TR": 5,  # RB
            "BTN_SELECT": 6,  # Back
            "BTN_START": 7,  # Start
            "BTN_MODE": 8,  # Guide
            "BTN_THUMBL": 9,  # LS
            "BTN_THUMBR": 10,  # RS
        }

        self._axis_values: dict[int, float] = {}
        self._button_values: dict[int, bool] = {}
        self._short_presses: list[str] = []
        self._long_presses: list[str] = []
        self._raw_events: list[str] = []
        self._down_times: dict[int, float] = {}

    def connect(self) -> bool:
        try:
            from inputs import devices  # type: ignore[import-untyped]
        except ImportError:
            return False
        gamepads = list(devices.gamepads)
        if not gamepads:
            self._device = None
            return False
        self._device = gamepads[0]
        self._axis_values.clear()
        self._button_values.clear()
        return True

    @property
    def connected(self) -> bool:
        return self._device is not None

    @property
    def name(self) -> str:
        return getattr(self._device, "name", "Unknown") if self._device else ""

    def maybe_reconnect(self) -> None:
        now = time.time()
        if not self.connected and now - self._last_reconnect > config.RECONNECT_INTERVAL:
            self._last_reconnect = now
            self.connect()

    def poll(self) -> GamepadState:
        self._process_events()
        short_presses, self._short_presses = self._short_presses, []
        long_presses, self._long_presses = self._long_presses, []
        raw_events, self._raw_events = self._raw_events, []

        if not self.connected:
            return GamepadState(
                connected=False,
                short_presses=short_presses,
                long_presses=long_presses,
            )

        # Build axis dict from config names
        axes: dict[str, float] = {}
        for name, idx in config.CONTROLS["axes"].items():
            axes[name] = self._axis_values.get(idx, 0.0)

        # Build button dict from config names
        buttons: dict[str, bool] = {}
        for name, idx in config.CONTROLS["buttons"].items():
            buttons[name] = self._button_values.get(idx, False)

        return GamepadState(
            connected=True,
            name=self.name,
            axes=axes,
            buttons=buttons,
            short_presses=short_presses,
            long_presses=long_presses,
            raw_events=raw_events,
        )

    def _process_events(self) -> None:
        if not self._device:
            return
        try:
            events = self._device.read()
        except Exception:
            self._device = None
            return

        for ev in events:
            if ev.ev_type == "Sync":
                continue

            # Map axis events
            axis_idx = self._INPUTS_AXES.get(ev.code)
            if axis_idx is not None:
                # inputs returns 0-255 or -32768-32767; normalise to -1..1
                if ev.state >= 0 and ev.state <= 255:
                    normalised = (ev.state - 127.5) / 127.5
                else:
                    normalised = ev.state / 32767.0
                normalised = max(-1.0, min(1.0, normalised))
                self._axis_values[axis_idx] = normalised
                name = config.CONTROLS["axes"].get(axis_idx, "")
                label = f"Axis {axis_idx}" if not name else f"Axis {axis_idx} ({name})"
                self._raw_events.append(f"{label} = {normalised:.3f}")
                continue

            # Map button events
            btn_idx = self._INPUTS_BUTTONS.get(ev.code)
            if btn_idx is not None:
                pressed = ev.state == 1
                if pressed:
                    self._down_times[btn_idx] = time.time()
                    self._button_values[btn_idx] = True
                    name = config.CONTROLS["buttons"].get(btn_idx, "")
                    label = f"Btn {btn_idx}" if not name else f"Btn {btn_idx} ({name})"
                    self._raw_events.append(label + " ↓")
                else:
                    self._button_values[btn_idx] = False
                    name = config.CONTROLS["buttons"].get(btn_idx, "")
                    label = f"Btn {btn_idx}" if not name else f"Btn {btn_idx} ({name})"
                    self._raw_events.append(label + " ↑")
                    if btn_idx in self._down_times:
                        duration = time.time() - self._down_times.pop(btn_idx)
                        if name and duration > config.LONG_PRESS_TIME:
                            self._long_presses.append(name)
                        elif name:
                            self._short_presses.append(name)
                continue

            # HAT / D-pad
            if ev.code.startswith("ABS_HAT"):
                self._raw_events.append(f"Hat {ev.code} = {ev.state}")

    def get_axis(self, name: str) -> float:
        for cfg_name, idx in config.CONTROLS["axes"].items():
            if cfg_name == name:
                return self._axis_values.get(idx, 0.0)
        return 0.0

    def is_button_pressed(self, name: str) -> bool:
        for cfg_name, idx in config.CONTROLS["buttons"].items():
            if cfg_name == name:
                return self._button_values.get(idx, False)
        return False


# ---------------------------------------------------------------------------
# Unified Gamepad — tries pygame first, falls back to inputs
# ---------------------------------------------------------------------------


class Gamepad:
    """Reads a physical game controller.

    Tries the pygame backend first (works with most controllers).
    Falls back to the ``inputs`` library when pygame finds no controllers.
    """

    def __init__(self) -> None:
        self._backend: _PygameBackend | _InputsBackend | None = None
        self._using_inputs = False

    def connect(self) -> bool:
        # Try pygame first
        pg = _PygameBackend()
        if pg.connect():
            self._backend = pg
            self._using_inputs = False
            return True

        # Fallback to inputs
        ib = _InputsBackend()
        if ib.connect():
            self._backend = ib
            self._using_inputs = True
            return True

        self._backend = None
        return False

    @property
    def connected(self) -> bool:
        if self._backend is None:
            return False
        if self._using_inputs:
            return self._backend.connected
        return self._backend.connected

    @property
    def name(self) -> str:
        return self._backend.name if self._backend else ""

    def poll(self) -> GamepadState:
        if self._backend is None:
            return GamepadState(connected=False)
        return self._backend.poll()

    def get_axis(self, name: str) -> float:
        if self._backend is None:
            return 0.0
        if self._using_inputs:
            return self._backend.get_axis(name)
        # Pygame backend: look up axis index and read it
        for cfg_name, idx in config.CONTROLS["axes"].items():
            if cfg_name == name:
                try:
                    return self._backend._joystick.get_axis(idx)
                except (pygame.error, AttributeError):
                    return 0.0
        return 0.0

    def is_button_pressed(self, name: str) -> bool:
        if self._backend is None:
            return False
        if self._using_inputs:
            return self._backend.is_button_pressed(name)
        for cfg_name, idx in config.CONTROLS["buttons"].items():
            if cfg_name == name:
                try:
                    return self._backend._joystick.get_button(idx) == 1
                except (pygame.error, AttributeError):
                    return False
        return False

    def maybe_reconnect(self) -> None:
        if self._backend is not None:
            self._backend.maybe_reconnect()
