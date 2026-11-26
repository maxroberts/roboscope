#!/bin/bash
# Configure the roboscope camera Pi (Pi Zero W, Raspberry Pi OS Lite 32-bit, Trixie).
#
# Runs ON the Pi as root, from the folder deploy_camera_pi.sh copies over (the folder
# holding this script and rootfs/). Don't run it by hand on the Mac.
# Idempotent: safe to re-run after any edit in the repo.
#
# What it does:
#   1. Installs the packages roboscope needs on the Pi (unattended-upgrades and the
#      camera stack: python3-picamera2, numpy, simplejpeg).
#   2. Copies rootfs/ over / (owner root, mode 644). Every file there says it is
#      deployed from the repo. The files are:
#      - etc/ssh/sshd_config.d/10-roboscope-keyonly.conf: key-only SSH, no root login.
#      - etc/apt/apt.conf.d/20auto-upgrades: daily apt update + unattended-upgrade.
#      - etc/apt/apt.conf.d/52roboscope-unattended-upgrades: allow Raspbian/RPi origins.
#      - etc/systemd/system/roboscope-camera.service: the camera server.
#   3. Validates the sshd config before reloading sshd (a broken config is removed, so
#      SSH is never left unable to start).
#   4. Disables Bluetooth (service off now; dtoverlay=disable-bt from the next boot).
#   5. Installs the camera server code (staged roboscope_camera/ → /opt/roboscope) and
#      (re)starts roboscope-camera.service (unit in rootfs/etc/systemd/system).
# Reboot is left to the caller; the script prints whether one is needed.
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
# Camera stack from apt, not pip/uv: picamera2 binds to the system libcamera, and
# armv6 wheels for numpy etc. would have to be compiled on the Zero. No recommends:
# they pull in Qt/GUI preview packages.
apt-get install -y -q --no-install-recommends unattended-upgrades \
    python3-picamera2 python3-numpy python3-simplejpeg >/dev/null

echo "== config files"
# Files are listed relative to rootfs/ and installed at the same path under /.
(cd rootfs && find . -type f) | while read -r f; do
    install -D -o root -g root -m 644 "rootfs/$f" "/${f#./}"
    echo "  /${f#./}"
done

echo "== sshd"
if sshd -t; then
    systemctl reload ssh
    sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin) '
else
    rm -f /etc/ssh/sshd_config.d/10-roboscope-keyonly.conf
    echo "sshd config invalid; removed our drop-in, sshd NOT reloaded" >&2
    exit 1
fi

echo "== bluetooth"
systemctl disable --now bluetooth.service hciuart.service 2>/dev/null || true
CFG=/boot/firmware/config.txt
reboot_needed=0
if ! grep -q '^dtoverlay=disable-bt' "$CFG"; then
    # The stock file ends in an [all] section, so a line appended there applies to
    # every model.
    printf '\n# roboscope: Bluetooth off (deployed by provision.sh)\n[all]\ndtoverlay=disable-bt\n' >> "$CFG"
    reboot_needed=1
fi

echo "== camera server"
# Code: src/roboscope/camera from the repo, staged by deploy_camera_pi.sh as
# roboscope_camera/ (it uses relative imports, so the package name doesn't matter).
if [ -d roboscope_camera ]; then
    rm -rf /opt/roboscope/roboscope_camera
    mkdir -p /opt/roboscope
    cp -r roboscope_camera /opt/roboscope/
    chown -R root:root /opt/roboscope
    chmod -R u=rwX,go=rX /opt/roboscope
    systemctl daemon-reload
    systemctl enable roboscope-camera.service >/dev/null 2>&1
    systemctl restart roboscope-camera.service
    sleep 5
    systemctl is-active roboscope-camera.service \
        || journalctl -u roboscope-camera.service -n 20 --no-pager
fi

echo "== unattended-upgrades dry run"
unattended-upgrade --dry-run 2>&1 | grep -E 'Allowed origins|No packages|Packages that will be upgraded' || true

# A newer kernel of the running flavour (e.g. -rpi-v6) installed means a reboot is due.
# The image ships v6/v7/v8 kernels side by side, so compare within the flavour only.
running=$(uname -r)
newest=$(ls /lib/modules | grep -- "-${running##*-}\$" | sort -V | tail -1)
if [ $reboot_needed -eq 1 ] || [ -f /var/run/reboot-required ] \
   || [ "$running" != "$newest" ]; then
    echo "REBOOT NEEDED"
fi
echo "provision done"
