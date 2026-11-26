# Camera Pi: deployable configuration

The Pi Zero W + OV5647 camera at the focuser (hostname `roboscopeCam`, reached as
`roboscopeCam.local` over mDNS). This folder is the source of truth for its setup: a
freshly flashed card plus `scripts/deploy_camera_pi.sh` should reproduce it. Don't
edit config on the Pi directly; edit it here and redeploy.

## Contents

| Path | What it is |
|---|---|
| `provision.sh` | Runs on the Pi as root. Installs packages (incl. the camera stack from apt), copies `rootfs/` over `/`, validates and reloads sshd, disables Bluetooth, sets up the USB gadget link (replacing the stock `rpi-usb-gadget` setup), moves the Wi-Fi connection out of netplan, turns on systemd linger for `max` (so the first SSH login after a gap isn't ~5 s slower), installs the camera server to `/opt/roboscope` and restarts its service, prints `REBOOT NEEDED` when needed. Idempotent. Run it over Wi-Fi: the USB step can drop the USB link. |
| `rootfs/etc/ssh/sshd_config.d/10-roboscope-keyonly.conf` | Key-only SSH, no root login (overrides the Imager's `50-cloud-init.conf`). |
| `rootfs/etc/apt/apt.conf.d/20auto-upgrades` | Daily apt update + unattended-upgrade. |
| `rootfs/etc/apt/apt.conf.d/52roboscope-unattended-upgrades` | Lets unattended-upgrades install from the Raspbian and Raspberry Pi origins (the stock config only matches Debian's, so it would upgrade nothing). No automatic reboot. |
| `rootfs/etc/systemd/system/roboscope-camera.service` | Runs `python3 -m roboscope_camera.server --source pi` as `max` on 127.0.0.1:47623 (= `client.PORT`), restart on failure. |
| `rootfs/etc/udev/rules.d/87-roboscope-gadget-load.rules`, `rootfs/etc/modprobe.d/g_ether.conf` | Load the USB Ethernet gadget (`g_ether`) once the USB controller appears, **and open `usb0` in the same step**, with fixed MACs (Pi end `02:72:62:00:00:01`, host end `02:72:62:00:00:02`). Opening `usb0` more than a few seconds after the host enumerates the gadget often left the link one-way after a cold power-up; see the USB link section. |
| `rootfs/etc/udev/rules.d/88-roboscope-usb0-up-early.rules` | Opens `usb0` whenever it appears, for other load paths (the watchdog's reload, a manual `modprobe`). |
| `rootfs/etc/NetworkManager/system-connections/roboscope-usb0.nmconnection` | `usb0`: static 10.12.194.1/28, no gateway. Installed mode 600. |
| `rootfs/etc/udev/rules.d/86-roboscope-usb0-managed.rules` | Lets NetworkManager manage `usb0`. Its own `85-nm-unmanaged.rules` ignores every USB gadget interface, which left the link down on every boot. |
| `rootfs/etc/systemd/system/roboscope-usb-dhcp.service` | dnsmasq, DHCP only on `usb0`: gives the host 10.12.194.2, no router, no DNS. Replaces NetworkManager's "shared" mode, which turns on IP forwarding and NAT. |
| `rootfs/etc/systemd/system/roboscope-usb-watchdog.service`, `rootfs/usr/local/lib/roboscope/usb-link-watchdog.sh` | USB link watchdog. Every 10 s, while a host has the gadget configured, it pings 10.12.194.2. After 3 misses it reloads `g_ether`, then backs off (60 s, doubling to 30 min) while the link stays dead. It's a safety net: since rule 87, no cold power-up has needed it (0 of 12). |
| `rootfs/etc/rpi/swap.conf.d/90-roboscope.conf` | Swap to zram only. The default swap file was re-`mkswap`ed on every boot (~21 s). |
| `rootfs/etc/cloud/cloud-init.disabled` | cloud-init off after provisioning (~45 s per boot). A fresh card doesn't have this file, so the Imager's first-boot setup still runs. |
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
4. `scripts/deploy_camera_pi.sh <Wi-Fi IP>` from the repo root (asks for the Pi's sudo
   password). Use the Wi-Fi address rather than `roboscopeCam.local`, which can resolve
   to the USB link.
5. Reboot if it prints `REBOOT NEEDED`.

## Current state (2026-11-25)
- Raspberry Pi OS Lite 32-bit, Trixie (Debian 13), kernel 6.18.60+rpt-rpi-v6. Fully
  upgraded, then provisioned with the files above.
- Listening: SSH (22) and mDNS (5353) on all interfaces, plus DHCP (67) on `usb0`
  only. There's no firewall. The Pi sits behind the home router's
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

## USB link (2026-11-25)
A micro-USB **data** cable from the Zero's "USB" port (nearest the mini-HDMI, not PWR
IN) to the host powers the Zero and is also an Ethernet link (USB gadget, CDC-ECM on
macOS and Linux). Wi-Fi stays up for management and updates.
- Pi: `10.12.194.1` (`ssh max@10.12.194.1`, same host key as over Wi-Fi). Host:
  always `10.12.194.2`, with no gateway, so its internet route is unaffected.
- No IP forwarding or NAT on the Pi.
- **Fixed issue: one-way link after a cold power-up.**
  - **Symptom:** the host's frames stopped reaching the Zero, in 19 of 32 cold
    power-ups when `usb0` was opened 12–23 s after the host enumerated the gadget.
  - **Fix:** rule 87 opens `usb0` within ~1 s. Since then, 0 of 12 cold power-ups
    have failed, and 0 of 11 with a ~5 s gap.
  - **Likely mechanism (not proven):** the host's 5 s transmit timeout cancels sends
    while the Zero is still refusing them, which wedges the Zero's receive endpoint.
  - The watchdog stays as a safety net.
  - **Link timing:** working ~90–95 s after a cold power-on, all with the 3B+ as host.
    Not yet re-tested with the Mac as host.
  - Details: `documentation/hardware_findings.md`, "2026-11-26 late morning".
- Measured Zero → Mac: plain TCP 14 MB/s (the Zero's CPU is the limit), SSH 4.5 MB/s,
  round trip 0.8 ms. Over Wi-Fi: 0.8–2.6 MB/s plain, ~0.5 MB/s SSH. Details and the
  reboot tests: `documentation/hardware_findings.md` ("2026-11-25 evening: camera Pi USB link…").
- The camera server still listens only on loopback; reach it through an SSH tunnel over
  the link. `roboscope.camera.connect()` and `scripts/camera_view.py` pick the USB link
  automatically when it answers.
  - Measured from the 3B+ (2026-11-26): raw frame ~0.35 s over USB against 4–22 s over
    Wi-Fi; `locate` ~0.3 s against 0.5–0.65 s.

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
  - streaming runs on the Pi, because sending raw frames over Wi-Fi would cost more:
    SSH over Wi-Fi moves ~0.5–1.5 MB/s (it varies), and a raw binned frame is 630 KB against
    a ~75 KB preview JPEG (lensless test frames; a dark sky compresses much better);
  - the preview defaults to 2 fps at JPEG quality 70;
  - the MJPEG preview reaches ≈ 2 fps through the tunnel (on the Zero, preview rendering
    takes ~90 ms and JPEG encoding ~105 ms per frame).
- Sensor facts used by the code:
  - raw SGBRG10 unpacked, right-aligned 0..1023;
  - exposure 0.13 ms to 3.07 s, gain 1 to 63.9;
  - controls take effect 3 frames after they're set.
  The probe that measured these was a one-off and isn't kept.
