# Camera Pi: state as found (2026-11-25)

Snapshot of the existing Pi Zero W camera rig before any changes. It records what was
there so it can be rebuilt or compared later. Copies of its files are in
`hardware/camera_pi/legacy_2019/` (passwords redacted). A full unredacted backup
(home directory, edited /etc configs, /boot config, package lists) was saved outside
the repo before the card was replaced. The original SD card is kept as well.

Decision (2026-11-25): replace the card with a fresh current Raspberry Pi OS Lite
install rather than updating Buster in place. Done the same day. The new setup and how
to rebuild it are in `hardware/camera_pi/README.md`. Everything below describes the
**old** card.

## Access
- Host `roboscopeCam`, 192.168.0.200 on the home Wi-Fi (wlan0, DHCP via 192.168.0.1;
  also has an IPv6 ULA and link-local address). SSH key login works for user `max`
  (two keys in `authorized_keys`: an older RSA one and the current ed25519 one).
- `sudo` asks for a password (no NOPASSWD), so every admin step needs the user.
- `/etc/hosts` is malformed: `127.0.1.1 raspberrypi` with `roboscopeCam` on its own
  line. So `sudo` warns "unable to resolve host roboscopeCam". This is harmless, but
  should be fixed.

## Hardware
- Raspberry Pi Zero W Rev 1.1 (ARMv6, 512 MB, 369 MiB visible with `gpu_mem=128`).
- Camera: OV5647 (Pi Camera v1, 5 MP, 2592×1944). `vcgencmd get_camera` →
  supported=1 detected=1. The EXIF in the 2019 samples says `RP_ov5647`.
- SD card: 7.1 GB root, 28% used. 42 °C idle, `get_throttled=0x0`.

## Software
- Raspbian 10 (Buster), kernel 4.19.75+ (Sep 2019). GPU firmware is from Sep 2019.
  Last real configuration was 2019-12-01. 63 packages are upgradable.
- Debian 10 is end of life: regular security support ended 2022 and LTS ended
  2024-06-30. Upgrading packages within Buster does not bring it back into support.
- Python 3.7.3. The legacy camera stack (`raspistill`, `raspivid`, MMAL;
  `start_x=1`) is present. There is no libcamera/`rpicam-*`. `picamera` was
  pip-installed for Python 2 (`sudo pip install picamera`). PIL is not installed
  for python3.
- apt sources: the Raspbian and RPi Buster repos, plus `linux-projects.org`
  **stretch** UV4L repo (third party).
- Enabled services of note:
  - `ssh`;
  - `uv4l_raspicam` (see below);
  - `avahi-daemon` (mDNS);
  - `bluetooth` + `hciuart`;
  - `rsync` (enabled but not listening);
  - `triggerhappy`;
  - the apt-daily timers.
- No user crontab. `rc.local` only prints the IP.

## Network exposure
Listening sockets:
- TCP 22 (sshd, all interfaces, IPv4+IPv6).
- TCP 8080 (UV4L web server, IPv4).
- UDP 5353 (mDNS) and 68 (DHCP client), plus two ephemeral UDP ports.

There's no firewall (`iptables`/`ufw`/`nft` not checked: they need sudo).

## UV4L camera stream (port 8080)
- `/etc/systemd/system/uv4l_raspicam.service` runs `uv4l` with
  `/etc/uv4l/uv4l-raspicam.conf` at boot.
- Settings: MJPEG, 640×480, 5 fps, quality 10, hflip+vflip, a local preview window.
  HTTP basic auth (user/admin password in plaintext in the config). WebRTC options
  are set.
- UV4L is a closed-source third-party package (v1.9.x, from the stretch repo).
- `resetGarageCam.sh` (comment "For CarlCam") restarts UV4L by hand at 20 fps with
  the same password. History shows the Pi was also used to view other cameras'
  streams (omxplayer/VLC). This was a general-purpose webcam setup, not telescope
  code.

## Files copied (`hardware/camera_pi/legacy_2019/`)

| File | What it is |
|---|---|
| `camera_test.py` | 9-line `picamera` capture at 1024×768 (produced `foo.jpg`) |
| `foo.jpg`, `test.jpg` | 2019-12-01 test shots of a ceiling (1024×768 and 2592×1944, stock lens) |
| `resetGarageCam.sh` | Manual UV4L restart; passwords → `REDACTED` |
| `uv4l-raspicam.conf` | Full UV4L config. Every `password=` value → `REDACTED`, including commented-out placeholders |
| `uv4l_raspicam.service` | The systemd unit |

Not copied:
- `TMdownlink_onboard (reduced).mp4` (3 MB, unrelated video);
- five 2019 `wget-log*` files (failed stream fetches; they contain only LAN IPs);
- `.bash_history` (summarized above, and it contains the password).

## Security notes
- The UV4L password appears in plaintext in the config, the script and the shell
  history. Treat it as exposed.
- What's actually wrong with the OS: an EOL distribution and 2019 kernel and
  firmware; a third-party closed-source web server on the LAN; Bluetooth on;
  password-based SSH probably allowed (sshd_config not readable without sudo).
