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
# XInput backend (Windows-native — works with Xbox 360/One compatibles)
# ---------------------------------------------------------------------------


class _XInputBackend:
    """Gamepad reader using Windows XInput API via ctypes.

    This is the most reliable backend on Windows for Xbox-compatible
    controllers (including the 8BitDo Ultimate 2C which presents as
    "Xbox 360 Controller for Windows").  It bypasses both SDL and
    the ``inputs`` library entirely.
    """

    def __init__(self) -> None:
        self._connected = False
        self._name = ""
        self._port = 0
        self._last_reconnect: float = 0.0
        self._axes: dict[int, float] = {}
        self._buttons: dict[int, bool] = {}
        self._short_presses: list[str] = []
        self._long_presses: list[str] = []
        self._down_times: dict[int, float] = {}
        self._prev_buttons: int = 0
        self._raw_events: list[str] = []
        self._xinput = None
        import ctypes
        for dll in ["xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"]:
            try:
                self._xinput = ctypes.windll.LoadLibrary(dll)
                break
            except Exception:
                continue

    # ---- Struct definitions (lazy) ----

    _XINPUT_GAMEPAD = None

    @staticmethod
    def _make_state():
        import ctypes

        class XINPUT_GAMEPAD(ctypes.Structure):
            _fields_ = [
                ("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short),
            ]

        class XINPUT_STATE(ctypes.Structure):
            _fields_ = [
                ("dwPacketNumber", ctypes.c_ulong),
                ("Gamepad", XINPUT_GAMEPAD),
            ]

        return XINPUT_STATE()

    def connect(self) -> bool:
        if self._xinput is None:
            return False
        import ctypes
        for port in range(4):
            state = self._make_state()
            try:
                result = self._xinput.XInputGetState(
                    ctypes.c_ulong(port), ctypes.byref(state)
                )
            except Exception:
                continue
            if result == 0:  # ERROR_SUCCESS
                self._connected = True
                self._name = f"XInput Controller (port {port})"
                self._port = port
                return True
        self._connected = False
        return False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return self._name if self._connected else ""

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
            return GamepadState(connected=False)

        axes: dict[str, float] = {}
        for name, idx in config.CONTROLS["axes"].items():
            axes[name] = self._axes.get(idx, 0.0)

        buttons: dict[str, bool] = {}
        for name, idx in config.CONTROLS["buttons"].items():
            buttons[name] = self._buttons.get(idx, False)

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
        if not self._connected or self._xinput is None:
            return
        import ctypes
        state = self._make_state()
        try:
            result = self._xinput.XInputGetState(
                ctypes.c_ulong(self._port), ctypes.byref(state)
            )
        except Exception:
            self._connected = False
            return
        if result != 0:
            self._connected = False
            return

        gp = state.Gamepad

        # Normalise axes to -1..1 matching SDL convention.
        # XInput Y-axes are inverted relative to SDL:
        #   SDL:  up = -1,  down = +1
        #   XInput: up = +1, down = -1
        # We negate Y values to match what the PTZ controller expects.
        self._axes[0] = max(-1.0, min(1.0, gp.sThumbLX / 32767.0))   # LEFT_X
        self._axes[1] = max(-1.0, min(1.0, -gp.sThumbLY / 32767.0))  # LEFT_Y  (negated)
        self._axes[2] = max(-1.0, min(1.0, gp.sThumbRX / 32767.0))   # RIGHT_X
        self._axes[3] = max(-1.0, min(1.0, -gp.sThumbRY / 32767.0))  # RIGHT_Y (negated)
        self._axes[4] = (gp.bRightTrigger / 127.5) - 1.0  # RIGHT_TRIGGER
        self._axes[5] = (gp.bLeftTrigger / 127.5) - 1.0   # LEFT_TRIGGER

        buttons = gp.wButtons
        now = time.time()

        # XInput button bitmask -> index mapping (matches config axis layout)
        xbtn = {
            0x1000: 0,  # A
            0x2000: 1,  # B
            0x4000: 2,  # X
            0x8000: 3,  # Y
            0x0010: 8,  # START
            0x0020: 9,  # BACK
            0x0040: 10,  # LEFT_THUMB
            0x0080: 11,  # RIGHT_THUMB
            0x0100: 12,  # LEFT_SHOULDER
            0x0200: 13,  # RIGHT_SHOULDER
        }

        for mask, btn_idx in xbtn.items():
            was_pressed = (self._prev_buttons & mask) != 0
            is_pressed = (buttons & mask) != 0
            self._buttons[btn_idx] = is_pressed
            if is_pressed and not was_pressed:
                self._down_times[btn_idx] = now
                self._raw_events.append(f"Btn {btn_idx} down")
            elif not is_pressed and was_pressed:
                self._raw_events.append(f"Btn {btn_idx} up")
                if btn_idx in self._down_times:
                    duration = now - self._down_times.pop(btn_idx)
                    name = config.CONTROLS["buttons"].get(btn_idx, "")
                    if name and duration > config.LONG_PRESS_TIME:
                        self._long_presses.append(name)
                    elif name:
                        self._short_presses.append(name)

        self._prev_buttons = buttons

    def get_axis(self, name: str) -> float:
        for cfg_name, idx in config.CONTROLS["axes"].items():
            if cfg_name == name:
                return self._axes.get(idx, 0.0)
        return 0.0

    def is_button_pressed(self, name: str) -> bool:
        for cfg_name, idx in config.CONTROLS["buttons"].items():
            if cfg_name == name:
                return self._buttons.get(idx, False)
        return False


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
        # Initialise display first — on Windows, SDL needs a window HWND
        # to register for Raw Input device enumeration.  Without this,
        # SDL can't detect any joysticks when running from pythonw.exe
        # (no console window to serve as a message pump).
        try:
            pygame.display.quit()
            pygame.display.init()
            # Create a window for SDL raw input registration, then minimise
            # it.  On Windows, SDL needs a visible (or at least mapped) HWND
            # to register for Raw Input device enumeration.  A HIDDEN window
            # doesn't work — the window must be mapped to the window station.
            pygame.display.set_mode((640, 480))
            pygame.display.iconify()
        except pygame.error:
            pass
        pygame.joystick.init()
        # Retry loop matching the legacy visca-joystick behaviour:
        # try Joystick(0) directly with short delays.
        # On Windows, get_count() can return 0 even when a controller
        # is connected, but Joystick(0) works once the window exists.
        for attempt in range(30):
            count = pygame.joystick.get_count()
            if count > 0:
                for idx in range(count):
                    try:
                        stick = pygame.joystick.Joystick(idx)
                        stick.init()
                        self._joystick = stick
                        return True
                    except pygame.error:
                        continue
            # Re-init joystick subsystem to force re-enumeration
            pygame.joystick.quit()
            pygame.joystick.init()
            time.sleep(0.5)
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
        # Pump the event queue before reading — prevents the
        # ``SystemError: <built-in function get> returned a result with
        # an exception set`` crash that occurs on Windows when the event
        # pump hasn't been initialised after a dummy-video-driver init.
        try:
            pygame.event.pump()
        except pygame.error:
            pass
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
            # Don't drop the device on read errors — on Windows the
            # ``inputs`` library can detect a controller but may fail
            # to read events from it.  Just skip this poll cycle.
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
        self._backend: _XInputBackend | _PygameBackend | _InputsBackend | None = None

    def connect(self) -> bool:
        # Try XInput first (most reliable on Windows for Xbox compatibles)
        xi = _XInputBackend()
        if xi.connect():
            self._backend = xi
            return True

        # Try pygame second
        pg = _PygameBackend()
        if pg.connect():
            self._backend = pg
            return True

        # Fallback to inputs
        ib = _InputsBackend()
        if ib.connect():
            self._backend = ib
            return True

        self._backend = None
        return False

    @property
    def connected(self) -> bool:
        if self._backend is None:
            return False
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
        if hasattr(self._backend, 'get_axis'):
            return self._backend.get_axis(name)
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
        if hasattr(self._backend, 'is_button_pressed'):
            return self._backend.is_button_pressed(name)
        for cfg_name, idx in config.CONTROLS["buttons"].items():
            if cfg_name == name:
                try:
                    return self._backend._joystick.get_button(idx) == 1
                except (pygame.error, AttributeError):
                    return False
        return False

    def maybe_reconnect(self) -> None:
        if self._backend is None:
            self.connect()
            return
        self._backend.maybe_reconnect()
