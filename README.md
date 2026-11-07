# ISDA Live Stream Tools

Web-based control tools for live streaming at ISDA: OBS control, PTZ camera
operation, overlay triggers, and a service-timestamp tracker.

## Features

- **OBS control** — WebSocket connection to OBS Studio: start stream/recording,
  autostart on a timer, live recording/streaming status with stats
- **GPU watchdog** — polls `nvidia-smi` and pops up a warning when the NVIDIA
  GPU is lost (Xid 79 / bus failure); NVENC then fails until reboot
- **PTZ camera controller** — gamepad-driven VISCA-over-IP camera control
  (pan/tilt/zoom, presets, exposure, focus) with a live status panel
- **Overlays** — trigger animated lower-third overlays in OBS via a transparent
  browser-source page (SSE)
- **Timestamps** — track service segments (song, sermon, prayer, …) with
  copyable timestamps and localStorage persistence

## Architecture

```
app.py              Flask backend: control UI, overlay SSE, PTZ status API
ptz_controller.py   Standalone process: gamepad → camera commands, writes state
ptz/                PTZ modules (config re-exports, camera, gamepad, state)
static/             Frontend (Tailwind CSS, OBS WebSocket JS)
templates/          index.html (control UI) + overlay.html (OBS browser source)
config.py           Loads all settings from config.yaml
```

## Configuration

**All configuration lives in `config.yaml`** (single source of truth):

1. Copy the template:
   ```bash
   cp config.example.yaml config.yaml
   ```
2. Edit `config.yaml` with your values:

   | Section | What it controls |
   |---|---|
   | `obs` | OBS Studio WebSocket IP, port and password |
   | `ptz.cameras` | VISCA-over-IP camera addresses |
   | `ptz.osee_*` | OSEE GoStream Deck host, port, transition packets |
   | `ptz.sensitivity` | Joystick → camera speed curves |
   | `ptz.controls` | Gamepad button/axis/hat mappings (pygame IDs) |
   | `gpu_watchdog` | NVIDIA GPU loss watchdog: poll interval, popup tool/text |
   | `app` | Flask host, port, debug mode |

> ⚠️ `config.yaml` is gitignored and contains real credentials — never commit it.

## Setup

```bash
uv sync          # Python dependencies (Flask, pygame, visca-over-ip, numpy, pyyaml)
npm install      # Node dev dependencies (Tailwind)
npm run build    # Build static/css/output.css from input.css
```

## Run

```bash
# Terminal 1 — web app
uv run python app.py

# Terminal 2 — PTZ controller (optional, needs a gamepad)
uv run python ptz_controller.py

# Terminal 3 — GPU watchdog (optional; on the NVIDIA livestream machine)
# Prefer deploying as a user service: deploy/isda-gpu-watchdog.service
uv run python gpu_watchdog.py
```

Then open <http://localhost:5000/> (control UI) and add
<http://localhost:5000/overlay> as an OBS browser source (transparent).

## Development

- CSS is Tailwind; edit `static/css/input.css`, then `npm run build`
- PTZ state is shared between processes via a JSON file in the OS temp dir

