#!/bin/bash
# Configure the roboscope host Pi (Pi 3B+, Raspberry Pi OS Lite 64-bit, Trixie).
#
# The host Pi runs roboscope itself: it drives the Arduino motor controller over USB
# serial and reaches the camera Pi Zero over the Zero's USB gadget link. This script
# runs ON the Pi as root (via sudo), from the folder scripts/deploy_host_pi.sh copies
# over (the folder holding this script and rootfs/). Don't run it by hand on the Mac.
# Idempotent: safe to re-run after any edit in the repo.
#
# What it does:
#   1. Installs packages: unattended-upgrades, git, uhubctl (switches power to the
#      USB ports, which can power-cycle the camera Zero; see hardware/host_pi/README.md),
#      avrdude (uploads firmware to the Uno, which is plugged in here;
#      scripts/upload_firmware_host_pi.sh).
#   2. Copies rootfs/ over / (owner root; mode 644, or 600 for NetworkManager keyfiles).
#      Every file there says it is deployed from the repo. The files are:
#      - etc/ssh/sshd_config.d/10-roboscope-keyonly.conf: key-only SSH, no root login.
#      - etc/apt/apt.conf.d/20auto-upgrades: daily apt update + unattended-upgrade.
#      - etc/apt/apt.conf.d/52roboscope-unattended-upgrades: also allow the Raspberry
#        Pi origin (Debian's is in the stock config).
#      - etc/NetworkManager/system-connections/roboscope-camlink.nmconnection: the USB
#        link to the camera Zero, static 10.12.194.2/28, bound to the gadget's MAC.
#      - Faster boot: etc/rpi/swap.conf.d/90-roboscope.conf (zram only, no swap file),
#        etc/cloud/cloud-init.disabled (cloud-init only matters on a fresh card).
#      - etc/systemd/user/roboscope-camera.service: the camera hub, a user service for
#        the login user (frames from the Zero over USB; preview, locate and UI here).
#      - etc/systemd/journald.conf.d/90-roboscope.conf: journal kept on the SD card
#        (max 100 MB), so power events and unclean resets can be traced after a reboot.
#   3. Validates the sshd config before reloading sshd (a broken config is removed, so
#      SSH is never left unable to start).
#   4. Disables Bluetooth (service off now; dtoverlay=disable-bt from the next boot).
#   5. Brings the camera link up now if the Zero is plugged in.
#   6. Moves the Wi-Fi connection out of netplan into a NetworkManager keyfile (same
#      step as hardware/camera_pi/provision.sh; there it saved ~55 s of every boot).
#   7. For the login user ($SUDO_USER, normally max): installs uv (official installer,
#      into ~/.local/bin) if missing, creates an SSH key (~/.ssh/id_ed25519) if missing
#      (deploy_host_pi.sh then authorizes it on the camera Zero), turns on linger and
#      enables the camera hub user service. deploy_host_pi.sh (re)starts the hub after
#      syncing the code, which the hub needs.
# Reboot is left to the caller; the script prints whether one is needed.
# Not handled here (known gap): the 3B+'s marginal power supply (see
# documentation/hardware_findings.md, 2026-11-26).
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
PI_USER=${SUDO_USER:-max}
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get install -y -q --no-install-recommends unattended-upgrades git uhubctl avrdude >/dev/null

echo "== config files"
# Files are listed relative to rootfs/ and installed at the same path under /.
(cd rootfs && find . -type f) | while read -r f; do
    mode=644
    case "$f" in */NetworkManager/system-connections/*) mode=600 ;; esac
    install -D -o root -g root -m "$mode" "rootfs/$f" "/${f#./}"
    echo "  /${f#./}"
done
systemctl daemon-reload
systemctl restart systemd-journald             # picks up the journald drop-in
journalctl --flush                             # move this boot's RAM journal to disk

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
    # The stock file ends in an [all] section; appending our own [all] keeps this
    # line applying to every model regardless.
    printf '\n# roboscope: Bluetooth off (deployed by provision.sh)\n[all]\ndtoverlay=disable-bt\n' >> "$CFG"
    reboot_needed=1
fi

echo "== camera link"
nmcli connection reload
# The profile binds by MAC; if the Zero is plugged in and booted, activate it now.
if ip -br link | grep -qi '02:72:62:00:00:02'; then
    nmcli -w 10 connection up roboscope-camlink >/dev/null || echo "  camera link not up yet" >&2
    ip -br -4 addr | grep 10.12.194 || true
else
    echo "  camera Zero not attached; the link comes up when it is"
fi

echo "== Wi-Fi: netplan -> NetworkManager keyfile"
# The Imager's Wi-Fi settings land in netplan (/etc/netplan/90-NM-*.yaml), and on every
# boot NetworkManager rewrites them, each write costing a `netplan generate` plus a
# systemd daemon-reload. A plain keyfile avoids that. The generated keyfile in /run
# already holds the full connection (the Wi-Fi password stays on the Pi), so copy it,
# then move the netplan files aside to /root/netplan-backup. Same UUID, so the active
# connection carries on.
shopt -s nullglob
yamls=(/etc/netplan/90-NM-*.yaml)
generated=(/run/NetworkManager/system-connections/netplan-wlan0-*.nmconnection)
if [ ${#yamls[@]} -gt 0 ] && [ ${#generated[@]} -gt 0 ]; then
    for src in "${generated[@]}"; do
        name=$(basename "$src"); dst="/etc/NetworkManager/system-connections/wifi-${name#netplan-wlan0-}"
        sed 's/^id=netplan-wlan0-/id=wifi-/' "$src" > "$dst"
        chmod 600 "$dst"
        echo "  $dst"
    done
    mkdir -p /root/netplan-backup
    mv "${yamls[@]}" /root/netplan-backup/
    rm -f /run/NetworkManager/system-connections/netplan-*.nmconnection
    nmcli connection reload
elif [ ${#yamls[@]} -gt 0 ]; then
    echo "  netplan config found but no generated Wi-Fi keyfile; left alone" >&2
fi
shopt -u nullglob

echo "== user setup ($PI_USER): uv, SSH key"
HOME_DIR=$(getent passwd "$PI_USER" | cut -d: -f6)
if [ ! -x "$HOME_DIR/.local/bin/uv" ]; then
    # Astral's documented installer; puts uv and uvx in ~/.local/bin.
    sudo -u "$PI_USER" -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
sudo -u "$PI_USER" -H "$HOME_DIR/.local/bin/uv" --version
if [ ! -f "$HOME_DIR/.ssh/id_ed25519" ]; then
    sudo -u "$PI_USER" -H ssh-keygen -q -t ed25519 -N '' -C "$PI_USER@$(hostname)" \
        -f "$HOME_DIR/.ssh/id_ed25519"
fi
echo "  key: $(cat "$HOME_DIR/.ssh/id_ed25519.pub")"
# Linger keeps the user's systemd manager running from boot, so user services (the
# camera hub) run without a login, and SSH logins don't wait for it to start.
loginctl enable-linger "$PI_USER"
uid=$(id -u "$PI_USER")
for _ in $(seq 1 20); do [ -S "/run/user/$uid/bus" ] && break; sleep 0.5; done
sudo -u "$PI_USER" XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user daemon-reload
sudo -u "$PI_USER" XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user enable roboscope-camera.service 2>&1 | grep -v "^Created symlink" || true

echo "== unattended-upgrades dry run"
unattended-upgrade --dry-run 2>&1 | grep -E 'Allowed origins|No packages|Packages that will be upgraded' || true

# A newer kernel of the running flavour (e.g. -rpi-v8) installed means a reboot is due.
# The image ships several kernel flavours side by side, so compare within the flavour.
running=$(uname -r)
newest=$(ls /lib/modules | grep -- "-${running##*-}\$" | sort -V | tail -1)
if [ $reboot_needed -eq 1 ] || [ -f /var/run/reboot-required ] \
   || [ "$running" != "$newest" ]; then
    echo "REBOOT NEEDED"
fi
echo "provision done"
