# Camera Pi: deployable configuration

The Pi Zero W + OV5647 camera at the focuser (hostname `roboscopeCam`, reached as
`roboscopeCam.local` over mDNS). This folder is the source of truth for its setup: a
freshly flashed card plus `scripts/deploy_camera_pi.sh` should reproduce it. Don't
edit config on the Pi directly; edit it here and redeploy.

## Contents

| Path | What it is |
|---|---|
| `provision.sh` | Runs on the Pi as root. Installs packages, copies `rootfs/` over `/`, validates and reloads sshd, disables Bluetooth, prints `REBOOT NEEDED` when needed. Idempotent. |
| `rootfs/etc/ssh/sshd_config.d/10-roboscope-keyonly.conf` | Key-only SSH, no root login (overrides the Imager's `50-cloud-init.conf`). |
| `rootfs/etc/apt/apt.conf.d/20auto-upgrades` | Daily apt update + unattended-upgrade. |
| `rootfs/etc/apt/apt.conf.d/52roboscope-unattended-upgrades` | Lets unattended-upgrades install from the Raspbian and Raspberry Pi origins (the stock config only matches Debian's, so it would upgrade nothing). No automatic reboot. |
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
- Not yet here: the roboscope camera service (capture on request, star centroid) and
  the Python dependencies for it.
