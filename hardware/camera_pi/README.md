# Camera Pi: deployable configuration

The Pi Zero W + OV5647 camera at the focuser (hostname `roboscopeCam`, reached as
`roboscopeCam.local` over mDNS). This folder is the source of truth for its setup: a
freshly flashed card plus `scripts/deploy_camera_pi.sh` should reproduce it. Don't
edit config on the Pi directly; edit it here and redeploy.

## Contents

| Path | What it is |
|---|---|
| `provision.sh` | Runs on the Pi as root. Installs packages (incl. the camera stack from apt), copies `rootfs/` over `/`, validates and reloads sshd, disables Bluetooth, installs the camera server to `/opt/roboscope` and restarts its service, prints `REBOOT NEEDED` when needed. Idempotent. |
| `rootfs/etc/ssh/sshd_config.d/10-roboscope-keyonly.conf` | Key-only SSH, no root login (overrides the Imager's `50-cloud-init.conf`). |
| `rootfs/etc/apt/apt.conf.d/20auto-upgrades` | Daily apt update + unattended-upgrade. |
| `rootfs/etc/apt/apt.conf.d/52roboscope-unattended-upgrades` | Lets unattended-upgrades install from the Raspbian and Raspberry Pi origins (the stock config only matches Debian's, so it would upgrade nothing). No automatic reboot. |
| `rootfs/etc/systemd/system/roboscope-camera.service` | Runs `python3 -m roboscope_camera.server --source pi` as `max` on 127.0.0.1:47623 (= `client.PORT`), restart on failure. |
| (from `src/roboscope/camera/`) | The camera server code. Not kept here: the deploy script copies it from the package to the Pi as `roboscope_camera/`. |
| `legacy_2019/` | Redacted copies of the old Buster setup (UV4L webcam), for reference only. Not deployed. |

## Rebuilding from scratch
1. Raspberry Pi Imager:
   - Device: Raspberry Pi Zero.
   - OS: Raspberry Pi OS Lite (32-bit).
   - Hostname: `roboscopeCam`.
   - User: `max`.
   - Wi-Fi and locale: yours.
   - SSH: on, public-key, with the Mac's `~/.ssh/id_ed25519.pub`.
2. Boot it, then run `ssh max@roboscopeCam.local true` once to accept the host key.
3. `sudo apt update && sudo apt full-upgrade` on the Pi (slow on a Zero: ~20 min).
4. `scripts/deploy_camera_pi.sh` from the repo root (asks for the Pi's sudo password).
5. Reboot if it prints `REBOOT NEEDED`.

## Current state (2026-11-25)
- Raspberry Pi OS Lite 32-bit, Trixie (Debian 13), kernel 6.18.60+rpt-rpi-v6. Fully
  upgraded, then provisioned with the files above.
- Listening: SSH (22) and mDNS (5353) only. There's no firewall. The Pi sits behind the home router's
  NAT (assumed to have no port forwards to it; not checked), and the OS is supported
  and auto-updating. So it isn't restricted to the LAN; apt needs outbound access.
- Camera: `rpicam-hello --list-cameras` shows the ov5647. Modes:
  - 640×480 at 62.5 fps
  - 1296×972 at 46 fps (2×2 binned)
  - 1920×1080 at 33 fps (crop)
  - 2592×1944 at 15.6 fps

  `rpicam-still` full-resolution capture takes ~4.7 s wall-clock, including a 2 s
  preview. Fixed exposures of 0.1/1/10 ms at gain 1 give black/dim/bright frames, so
  exposure control works.
- Every test frame was a featureless gradient with magenta corners, so no image was
  being formed (no lens, or not on the telescope at the time). The magenta comes from
  libcamera's lens-shading correction tuned for the stock lens. For astronomy, use raw
  or turn that correction off.

## Camera server
- `roboscope-camera.service` owns the camera from boot (only one process can). See
  `src/roboscope/camera/server.py` for the API.
- Reach it from the Mac:
  - browser: `uv run python scripts/camera_view.py`;
  - Python: `roboscope.camera.connect()`.
  The page is always at http://127.0.0.1:47623/ (port fixed in `client.PORT`).
  Both open an SSH tunnel, so nothing listens on the LAN.
- To use the camera directly (e.g. `rpicam-still`), first run
  `sudo systemctl stop roboscope-camera`.
- Logs: `journalctl -u roboscope-camera`.
- Python packages come from apt (`python3-picamera2`, `python3-numpy`,
  `python3-simplejpeg`): picamera2 needs the system libcamera, and ARMv6 has no
  prebuilt wheels. So the Pi doesn't use uv.
- Measured (2026-11-25, binned mode unless noted):
  - capture is capped at 10 fps (the frame period is at least 100 ms), where the
    server process uses ~40% of the CPU;
  - idle mode drops to 1 fps (~5% CPU) after 30 s with no requests and no open
    viewer; the next request wakes it;
  - locate takes ≈ 0.6–0.8 s (1 ms exposure), or ≈ 1.5 s at full resolution;
  - streaming runs on the Pi, because sending raw frames would cost more: SSH moves
    ~1.5 MB/s with the Zero's CPU saturated, and a raw binned frame is 630 KB against
    a ~75 KB preview JPEG (lensless test frames; a dark sky compresses much better);
  - the preview defaults to 2 fps at JPEG quality 70;
  - the MJPEG preview reaches ≈ 2 fps through the tunnel (on the Zero, preview rendering
    takes ~90 ms and JPEG encoding ~105 ms per frame).
- Sensor facts used by the code:
  - raw SGBRG10 unpacked, right-aligned 0..1023;
  - exposure 0.13 ms to 3.07 s, gain 1 to 63.9;
  - controls take effect 3 frames after they're set.
  The probe that measured these was a one-off and isn't kept.
