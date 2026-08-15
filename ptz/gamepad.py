"""
Game controller reader using pygame.

Wraps a physical game controller, maps buttons/axes/hats via config,
and provides an event-polling interface.
"""

import time
from dataclasses import dataclass, field

import pygame

from . import config

# -- Named axis/button/hat IDs for clean access ------------------------------


@dataclass(frozen=True)
class AxisID:
    name: str
    index: int


@dataclass(frozen=True)
class ButtonID:
    name: str
    index: int


@dataclass(frozen=True)
class HatID:
    name: str
    value: tuple[int, int]


# -- State snapshot -----------------------------------------------------------


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
    # Raw input discovery: every button/hat event, including unmapped
    raw_events: list[str] = field(default_factory=list)


# -- Controller ---------------------------------------------------------------


class Gamepad:
    """Reads a physical game controller via pygame."""

    def __init__(self):
        self._joystick = None

        # Pre-built lookup tables from config
        self._axis_ids: dict[int, AxisID] = {
            idx: AxisID(name, idx) for name, idx in config.CONTROLS["axes"].items()
        }
        self._button_ids: dict[int, ButtonID] = {
            idx: ButtonID(name, idx) for name, idx in config.CONTROLS["buttons"].items()
        }
        self._hat_ids: dict[tuple[int, int], HatID] = {
            tuple(v): HatID(name, tuple(v))
            for name, v in config.CONTROLS["hats"].items()
        }

        # State tracking
        self._down_times: dict[int, float] = {}  # button/hat index → press time
        self._short_presses: list[str] = []
        self._long_presses: list[str] = []
        self._short_hats: list[str] = []
        self._long_hats: list[str] = []
        self._raw_events: list[str] = []
        self._last_reconnect = 0.0

    # -- Connection -----------------------------------------------------------

    def connect(self) -> bool:
        """Try to connect to the first available joystick. Returns True on success."""
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self._joystick = None
            return False

        self._joystick = None
        try:
            stick = pygame.joystick.Joystick(0)
            stick.init()
        except pygame.error:
            return False

        self._joystick = stick
        return True

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

    # -- Polling --------------------------------------------------------------

    def _process_hat_motion(self, event):
        """Handle a single JOYHATMOTION event."""
        hat_value = tuple(event.value)
        hat_id = self._hat_ids.get(hat_value)
        label = f"Hat {hat_value}" if hat_id is None else f"Hat {hat_value} ({hat_id.name})"

        if hat_value == (0, 0):
            self._raw_events.append(label + " ·")
            for hidx, hat_name in self._hat_ids.items():
                if hidx in self._down_times:
                    duration = time.time() - self._down_times.pop(hidx)
                    (self._long_hats if duration > config.LONG_PRESS_TIME else self._short_hats).append(
                        hat_name.name,
                    )
                    break
        else:
            self._raw_events.append(label + " ↓")
            if hat_id and hat_id.value not in self._down_times:
                self._down_times[hat_id.value] = time.time()

    def _process_events(self):
        """Drain the pygame event queue, tracking press/release timings."""
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                idx = event.button
                self._down_times[idx] = time.time()
                name = self._button_ids.get(idx)
                label = f"Btn {idx}" if name is None else f"Btn {idx} ({name.name})"
                self._raw_events.append(label + " ↓")

            elif event.type == pygame.JOYBUTTONUP:
                idx = event.button
                name = self._button_ids.get(idx)
                label = f"Btn {idx}" if name is None else f"Btn {idx} ({name.name})"
                self._raw_events.append(label + " ↑")
                if idx in self._down_times:
                    duration = time.time() - self._down_times.pop(idx)
                    if name:
                        (self._long_presses if duration > config.LONG_PRESS_TIME else self._short_presses).append(
                            name.name,
                        )

            elif event.type == pygame.JOYHATMOTION:
                self._process_hat_motion(event)

    def poll(self) -> GamepadState:
        """Read current controller state and return a snapshot."""
        self._process_events()

        # Drain accumulated press lists
        short_presses = self._short_presses
        long_presses = self._long_presses
        short_hats = self._short_hats
        long_hats = self._long_hats
        raw_events = self._raw_events
        self._short_presses = []
        self._long_presses = []
        self._short_hats = []
        self._long_hats = []
        self._raw_events = []

        if not self.connected:
            return GamepadState(
                connected=False,
                short_presses=short_presses,
                long_presses=long_presses,
                short_hats=short_hats,
                long_hats=long_hats,
            )

        # Read current axes
        axes: dict[str, float] = {}
        for aid in self._axis_ids.values():
            try:
                axes[aid.name] = self._joystick.get_axis(aid.index)
            except (pygame.error, AttributeError):
                axes[aid.name] = 0.0

        # Read current buttons
        buttons: dict[str, bool] = {}
        for bid in self._button_ids.values():
            try:
                buttons[bid.name] = self._joystick.get_button(bid.index) == 1
            except (pygame.error, AttributeError):
                buttons[bid.name] = False

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

    def get_axis(self, name: str) -> float:
        """Get current value of a named axis."""
        if not self._joystick:
            return 0.0
        for aid in self._axis_ids.values():
            if aid.name == name:
                try:
                    return self._joystick.get_axis(aid.index)
                except (pygame.error, AttributeError):
                    return 0.0
        return 0.0

    def is_button_pressed(self, name: str) -> bool:
        """Check if a named button is currently held."""
        if not self._joystick:
            return False
        for bid in self._button_ids.values():
            if bid.name == name:
                try:
                    return self._joystick.get_button(bid.index) == 1
                except (pygame.error, AttributeError):
                    return False
        return False

    # -- Maintenance -----------------------------------------------------------

    def maybe_reconnect(self):
        """Periodically attempt to reconnect if disconnected."""
        now = time.time()
        if not self.connected and now - self._last_reconnect > config.RECONNECT_INTERVAL:
            self._last_reconnect = now
            self.connect()
