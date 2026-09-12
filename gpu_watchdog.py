#!/usr/bin/env python3
"""
GPU Watchdog — standalone process (runs independently from Flask).

Polls ``nvidia-smi`` and signals the user when the NVIDIA GPU has been lost
(e.g. an Xid 79 "GPU has fallen off the bus" event, after which NVENC stream
encoding keeps failing until the machine is rebooted).

It only *shows a desktop popup* asking for a reboot — it never reboots the
machine itself; that decision is left to the operator.

Usage:
    uv run python gpu_watchdog.py              # daemon (systemd user service)
    uv run python gpu_watchdog.py --once       # single check, for scripts/tests

Exit codes (``--once`` only; the daemon keeps running otherwise):
    0  GPU healthy (or no NVIDIA GPU found)
    2  GPU degraded/lost
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

import config

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def gpu_healthy(nvidia_smi: str) -> bool:
    """
    Probe the NVIDIA driver through ``nvidia-smi``.

    When the GPU falls off the bus the driver can no longer talk to the card,
    so ``nvidia-smi`` exits non-zero (or enumerates no devices).  Any failed
    probe is treated as "GPU gone" — NVENC will not work until a reboot.
    """
    try:
        result = subprocess.run(  # noqa: S603 — path is CLI/config-controlled
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"GPU: probe error: {exc}")
        return False
    if result.returncode != 0:
        print(f"GPU: nvidia-smi exited {result.returncode}: {result.stderr.strip()}")
        return False
    name = result.stdout.strip()
    if not name:
        print("GPU: nvidia-smi returned no GPU (device lost?)")
        return False
    print(f"GPU: healthy ({name})")
    return True


# ---------------------------------------------------------------------------
# Popup
# ---------------------------------------------------------------------------


def _popup_argv(program: str) -> list[str] | None:
    """Argv template for a popup program, or None if the program is unknown."""
    templates = {
        "kdialog": ["kdialog", "--title", "{title}", "--sorry", "{text}"],
        "zenity": ["zenity", "--warning", "--title", "{title}", "--text", "{text}"],
        "notify-send": ["notify-send", "--urgency=critical", "{title}", "{text}"],
    }
    return templates.get(program)


def _resolve_popup_program(preferred: str) -> str:
    """Pick a usable popup tool: preferred → kdialog → zenity → notify-send."""
    candidates = [preferred] if preferred else []
    candidates += ["kdialog", "zenity", "notify-send"]
    for program in candidates:
        if program and shutil.which(program):
            return program
        if program:
            print(f"GPU: popup program '{program}' not found")
    return ""


def show_popup(argv: list[str], title: str, text: str, display: str) -> None:
    """Run the popup command with ``{title}``/``{text}`` placeholders filled."""
    filled = [arg.replace("{title}", title).replace("{text}", text) for arg in argv]
    try:
        subprocess.run(  # noqa: S603 — argv is built from known templates
            filled,
            env={**os.environ, "DISPLAY": display},
            timeout=15,
            check=False,
        )
        print("GPU: popup shown")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"GPU: popup failed: {exc}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


@dataclass
class Watchdog:
    """Stateful per-cycle logic: probe the GPU and pop up once per failure."""

    nvidia_smi: str
    popup_argv: list[str] | None
    title: str
    text: str
    display: str
    effective_failures: int
    consecutive: int = field(default=0)
    popup_shown: bool = field(default=False)

    def tick(self) -> bool:
        """Run one poll cycle. Returns True while the GPU is healthy."""
        if gpu_healthy(self.nvidia_smi):
            if self.popup_shown:
                print("GPU: GPU reachable again — resetting watchdog state")
            self.consecutive = 0
            self.popup_shown = False
            return True
        self.consecutive += 1
        if not self.popup_shown and self.consecutive >= self.effective_failures:
            print(
                f"GPU: GPU LOST ({self.consecutive} failed probes) — "
                "NVENC will fail until reboot",
            )
            if self.popup_argv is not None:
                show_popup(self.popup_argv, self.title, self.text, self.display)
            self.popup_shown = True
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="NVIDIA GPU loss watchdog")
    parser.add_argument(
        "--poll-interval", type=float, default=config.GPU_WATCHDOG_POLL_INTERVAL,
        help="seconds between nvidia-smi checks (default: %(default)s)",
    )
    parser.add_argument(
        "--failures", type=int, default=2,
        help="consecutive failed polls before showing the popup (default: %(default)s)",
    )
    parser.add_argument(
        "--nvidia-smi", default=shutil.which("nvidia-smi") or "",
        help="path to nvidia-smi (default: found on PATH)",
    )
    parser.add_argument(
        "--popup-command", default="",
        help="shell command with {title}/{text} placeholders (overrides auto-detect)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="check once and exit (0 = healthy, 2 = GPU lost)",
    )
    args = parser.parse_args()

    if not config.GPU_WATCHDOG_ENABLED:
        print("GPU: watchdog disabled in config.yaml (gpu_watchdog.enabled: false)")
        return 0
    if not args.nvidia_smi:
        print("GPU: nvidia-smi not found — no NVIDIA GPU on this machine, exiting")
        return 0

    title = config.GPU_WATCHDOG_POPUP_TITLE
    text = config.GPU_WATCHDOG_POPUP_TEXT
    display = os.environ.get("DISPLAY") or ":0"

    # Popup strategy: explicit shell command (testing/overrides) or auto-detect.
    popup_argv: list[str] | None
    if args.popup_command:
        # Quote placeholder values: ``sh -c`` would choke on metacharacters
        # (quotes, parens, …) in the title/text otherwise.
        command = (
            args.popup_command
            .replace("{title}", shlex.quote(title))
            .replace("{text}", shlex.quote(text))
        )
        popup_argv = ["sh", "-c", command]
    else:
        program = _resolve_popup_program(config.GPU_WATCHDOG_POPUP_PROGRAM)
        popup_argv = _popup_argv(program)
        if not popup_argv:
            print(
                "GPU: no popup tool available (kdialog/zenity/notify-send); "
                "logging only",
            )

    effective_failures = 1 if args.once else max(1, args.failures)
    watchdog = Watchdog(
        nvidia_smi=args.nvidia_smi,
        popup_argv=popup_argv,
        title=title,
        text=text,
        display=display,
        effective_failures=effective_failures,
    )

    while True:
        healthy = watchdog.tick()
        if args.once:
            return 0 if healthy else 2
        time.sleep(max(0.1, args.poll_interval))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("GPU: watchdog stopped")
        sys.exit(0)
