#!/usr/bin/env python3
"""
PTZ Camera Controller — standalone process (runs independently from Flask).

Reads a game controller via pygame, maps inputs to VISCA-over-IP camera
commands, and writes status to a shared JSON file that the web UI reads.

Usage:
    uv run python ptz_controller.py
"""

import os
import signal
import socket
import time
import traceback

# Set dummy video driver for headless operation (no display needed)
os.environ.setdefault("SDL_VIDEO_DRIVER", "dummy")

import pygame

from ptz import config
from ptz.camera import AxisMapper, PTZCamera, combine_pan
from ptz.gamepad import Gamepad
from ptz.state import PTZStatus, write_status


def _send_osee(hex_packet: str):
    """Send a raw transition command to the OSEE GoStream Deck."""
    try:
        with socket.create_connection(
            (config.OSEE_HOST, config.OSEE_PORT), timeout=1.0,
        ) as sock:
            sock.send(bytes.fromhex(hex_packet))
        print("PTZ: OSEE transition sent")
    except OSError as exc:
        print(f"PTZ: OSEE error: {exc}")


def _announce_controller(gamepad: Gamepad, status: PTZStatus):
    """Connect the gamepad and record the initial controller status."""
    if gamepad.connect():
        print(f"   Controller: {gamepad.name}")
        status.controller_connected = True
        status.controller_name = gamepad.name
        status.last_action = f"Controller connected: {gamepad.name}"
    else:
        print("   Controller: waiting (plug one in)")
        status.last_action = "Waiting for controller..."
    write_status(status)


def _sync_connection_status(status: PTZStatus, state) -> bool:
    """Copy controller state into status. Returns True if connection changed."""
    was_connected = status.controller_connected
    status.controller_connected = state.connected
    status.controller_name = state.name
    status.axes = state.axes
    status.buttons = state.buttons

    if state.connected and not was_connected:
        status.last_action = f"Controller connected: {state.name}"
        print(f"PTZ: {status.last_action}")
        return True
    if not state.connected and was_connected:
        status.last_action = "Controller disconnected"
        print(f"PTZ: {status.last_action}")
        return True
    return False


def _handle_button_press(btn: str, cam: PTZCamera | None, status: PTZStatus) -> bool:
    """Handle one short button press. Returns True if an action fired."""
    match btn:
        case "RESET_POSITION":
            if cam:
                cam.recall_preset(0)
                status.last_action = "Camera → Home position"
                print(f"PTZ: {status.last_action}")
                time.sleep(2)
                return True
        case "TRANSITION_CUT":
            _send_osee(config.OSEE_CUT_TRANSITION)
            status.last_action = "Cut transition"
            return True
        case "TRANSITION_AUTO":
            _send_osee(config.OSEE_AUTO_TRANSITION)
            status.last_action = "Auto transition"
            return True
    return False


def _handle_hat_press(hat: str, cam: PTZCamera | None, status: PTZStatus) -> bool:
    """Handle one short hat press. Returns True if an action fired."""
    match hat:
        case "EXPOSURE_UP":
            if cam:
                cam.increase_gain()
                status.last_action = "Exposure +"
                return True
        case "EXPOSURE_DOWN":
            if cam:
                cam.decrease_gain()
                status.last_action = "Exposure -"
                return True
        case "FOCUS_FAR":
            if cam:
                cam.set_focus_mode("manual")
                cam.manual_focus(-1)
                status.last_action = "Focus → Far"
                return True
        case "FOCUS_NEAR":
            if cam:
                cam.set_focus_mode("manual")
                cam.manual_focus(1)
                status.last_action = "Focus → Near"
                return True
    return False


def _read_axes_fresh(gamepad: Gamepad) -> dict[str, float]:
    """Read current axis values directly from the gamepad.

    Unlike ``state.axes`` which is captured once at the start of the
    loop, this reads the *live* values so that any delay from button
    handling doesn't make them stale.
    """
    axes: dict[str, float] = {}
    for name, idx in config.CONTROLS["axes"].items():
        axes[name] = gamepad.get_axis(name)
    return axes


def _drive_camera(
    cam: PTZCamera,
    mapper: AxisMapper,
    gamepad: Gamepad,
    buttons: dict[str, bool],
) -> None:
    """Continuous pan/tilt/zoom control from the joystick axes.

    Reads axis values **just in time** (matching the legacy visca-joystick
    behaviour) so that button-handling delays don't send stale commands.
    """
    sensitivity = cam.zoom_sensitivity_multiplier()
    quick_pan = buttons.get("QUICK_PAN", False)
    quick_zoom = buttons.get("QUICK_ZOOM", False)

    # Read live axis values right before sending commands
    pan_left = gamepad.get_axis("PAN_LEFT")
    pan_right = gamepad.get_axis("PAN_RIGHT")
    pan_stick = gamepad.get_axis("PAN")
    tilt = gamepad.get_axis("TILT")
    zoom = gamepad.get_axis("ZOOM")

    pan_speed = mapper.to_speed(
        combine_pan(pan_left, pan_right, pan_stick),
        "pan",
        invert=config.INVERT_PAN,
        speedup=quick_pan,
        multiplier=sensitivity,
    )
    tilt_speed = mapper.to_speed(
        tilt,
        "tilt",
        invert=config.INVERT_TILT,
        speedup=quick_pan,
        multiplier=sensitivity,
    )

    cam.pantilt(pan_speed, tilt_speed)
    time.sleep(config.POLL_INTERVAL)

    cam.zoom(
        mapper.to_speed(
            zoom,
            "zoom",
            invert=config.INVERT_ZOOM,
            speedup=quick_zoom,
        ),
    )


def _run_loop(gamepad: Gamepad, mapper: AxisMapper, cameras: dict[int, PTZCamera], status: PTZStatus):
    """Main control loop: poll gamepad, drive camera, publish status."""
    while True:
        gamepad.maybe_reconnect()
        state = gamepad.poll()

        _sync_connection_status(status, state)

        cam = cameras.get(0)
        status.camera_connected = cam.connected if cam else False

        if not state.connected:
            # Always update axes even when disconnected
            write_status(status)
            time.sleep(config.POLL_INTERVAL)
            continue

        # Update raw input display from gamepad events
        if state.raw_events:
            status.last_raw_input = " | ".join(state.raw_events[-3:])

        # Process short button/hat presses
        for btn in state.short_presses:
            _handle_button_press(btn, cam, status)
        for hat in state.short_hats:
            _handle_hat_press(hat, cam, status)

        # Continuous axis control — reads live axis values, not the
        # potentially-stale snapshot from ``state``.
        if cam and cam.connected:
            _drive_camera(cam, mapper, gamepad, state.buttons)

        write_status(status)
        time.sleep(config.POLL_INTERVAL)


def main():
    print("PTZ Camera Controller")
    print(f"   Cameras: {config.CAMERAS}")
    print("   Press Ctrl+C to stop")

    # Trap SIGTERM (systemd stop) for clean shutdown
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    pygame.init()
    pygame.joystick.init()

    gamepad = Gamepad()
    mapper = AxisMapper()

    # Camera setup
    cameras: dict[int, PTZCamera] = {}
    if config.CAMERAS:
        cameras[0] = PTZCamera(config.CAMERAS[0])

    # Initial state
    status = PTZStatus(
        axes=dict.fromkeys(config.CONTROLS["axes"], 0.0),
        buttons=dict.fromkeys(config.CONTROLS["buttons"], False),
    )
    if config.CAMERAS:
        status.camera_ip = config.CAMERAS[0]
    write_status(status)

    _announce_controller(gamepad, status)

    try:
        _run_loop(gamepad, mapper, cameras, status)
    except KeyboardInterrupt:
        print("\nPTZ: shutting down...")
    except Exception:  # noqa: BLE001 — top-level guard: log and exit cleanly
        traceback.print_exc()
    finally:
        for c in cameras.values():
            c.stop()
        status.last_action = "PTZ controller stopped"
        status.controller_connected = False
        write_status(status)
        pygame.quit()


if __name__ == "__main__":
    main()
