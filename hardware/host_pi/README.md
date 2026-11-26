# Host Pi: deployable configuration

The Raspberry Pi 3B+ that runs roboscope at the telescope (hostname `roboscope`, reached
as `roboscope.local`). It drives the Arduino motor controller over USB serial and
reaches the camera Pi Zero over the Zero's USB gadget link. This folder is the source of
truth for its setup: a freshly flashed card plus `scripts/deploy_host_pi.sh` should
reproduce it. Don't edit config on the Pi directly; edit it here and redeploy.

## Contents

| Path | What it is |
|---|---|
| `provision.sh` | Runs on the Pi as root. Installs packages (unattended-upgrades, git), copies `rootfs/` over `/`, validates and reloads sshd, disables Bluetooth, brings up the camera link, moves the Wi-Fi connection out of netplan, installs uv and creates an SSH key for the login user, prints `REBOOT NEEDED` when needed. Idempotent. |
| `rootfs/etc/ssh/sshd_config.d/10-roboscope-keyonly.conf` | Key-only SSH, no root login (overrides the Imager's `50-cloud-init.conf`). |
| `rootfs/etc/apt/apt.conf.d/20auto-upgrades` | Daily apt update + unattended-upgrade. |
| `rootfs/etc/apt/apt.conf.d/52roboscope-unattended-upgrades` | Also lets unattended-upgrades install from the Raspberry Pi origin (Debian's is already in the stock config). No automatic reboot. |
| `rootfs/etc/NetworkManager/system-connections/roboscope-camlink.nmconnection` | The USB link to the camera Zero: static 10.12.194.2/28, no gateway, bound to the gadget's fixed host-end MAC `02:72:62:00:00:02`. Static because the 3B+ boots faster than the Zero, so DHCP from the Zero could time out. Installed mode 600. |
| `rootfs/etc/rpi/swap.conf.d/90-roboscope.conf` | Swap to zram only, no swap file. |
| `rootfs/etc/systemd/journald.conf.d/90-roboscope.conf` | Journal kept on the SD card (max 100 MB) instead of RAM only, so power events and unclean resets can be traced after a reboot. |
| `rootfs/etc/cloud/cloud-init.disabled` | cloud-init off after provisioning. A fresh card doesn't have this file, so the Imager's first-boot setup still runs. |

`scripts/deploy_host_pi.sh` (on the Mac) pushes this folder and runs `provision.sh`.
It then authorizes the 3B+'s SSH key on the camera Zero and records the Zero's host key
on the 3B+. Last, it syncs the repo to `~/roboscope` and runs `uv sync --dev`.
`--code-only` repeats just the code sync.

## Rebuilding from scratch
1. Raspberry Pi Imager:
   - Device: Raspberry Pi 3.
   - OS: Raspberry Pi OS Lite (64-bit).
   - Hostname: `roboscope`.
   - User: `max`.
   - Wi-Fi and locale: yours.
   - SSH: on, public-key, with the Mac's `~/.ssh/id_ed25519.pub`.
2. Boot it, then run `ssh max@roboscope.local true` once to accept the host key.
3. `scripts/deploy_host_pi.sh <3B+ IP> <Zero Wi-Fi IP>` from the repo root. It asks for
   the 3B+'s sudo password.
4. Reboot if it prints `REBOOT NEEDED`.

## Current state (2026-11-26)
- Raspberry Pi OS Lite 64-bit, Trixie, kernel 6.18.50+rpt-rpi-v8. SD card: the
  32 GB "test" card (checked with f3; slow writes; see
  `documentation/hardware_findings.md`).
- USB: the Uno at `/dev/ttyACM0`, and the camera Zero (link 10.12.194.1 ↔ .2). The 3B+
  logs in to the Zero as `ssh max@10.12.194.1` with its own key.
- Boot takes ~18 s.
- The camera link works ~90–95 s after a cold power-on. The earlier one-way failure is
  fixed on the Zero's side (`hardware/camera_pi`, udev rule 87), with its watchdog as a
  safety net.
- `uhubctl -l 1-1 -p 2 -a off` / `on` switches power to **all** the 3B+'s USB ports
  together, so it cold-boots the Zero, and also cuts the Uno. Port-level switching
  (`-l 1-1.1 -p 3`) doesn't actually cut the power.
- `uv run pytest` passes here: the full suite takes ~5 min, against ~26 s on the Mac.
- **Power is marginal.** Every supply tried trips the Pi's under-voltage flag under
  load, which slows the CPU from 1.4 GHz to 600 MHz. It runs from a bench supply at
  5.25 V for now; the Uno and the Zero are powered from the Pi's USB ports. Details:
  hardware_findings.md, "2026-11-26: host Pi (3B+) bring-up and its power supply".
  Check with `vcgencmd get_throttled` (`0x0` is healthy).
