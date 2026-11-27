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
#   2. Copies rootfs/ over / (owner root; mode 644, or 600 for NetworkManager
#      keyfiles). Every file there says it is deployed from the repo. The files are:
#      - etc/ssh/sshd_config.d/10-roboscope-keyonly.conf: key-only SSH, no root login.
#      - etc/apt/apt.conf.d/20auto-upgrades: daily apt update + unattended-upgrade.
#      - etc/apt/apt.conf.d/52roboscope-unattended-upgrades: allow Raspbian/RPi origins.
#      - etc/systemd/system/roboscope-camera.service: the camera server.
#      - USB gadget link to the host computer (USB Ethernet over the Zero's data port):
#        etc/udev/rules.d/87-roboscope-gadget-load.rules (load g_ether once the USB
#        controller exists and bring usb0 up at once: opening usb0 late after a cold
#        power-up often left the link one-way; see that rule),
#        etc/udev/rules.d/88-roboscope-usb0-up-early.rules (usb0 up as soon as it
#        appears, for any other load path such as the watchdog's reload),
#        etc/modprobe.d/g_ether.conf (fixed MACs),
#        etc/NetworkManager/system-connections/roboscope-usb0.nmconnection (static
#        10.12.194.1/28), etc/systemd/system/roboscope-usb-dhcp.service (gives the
#        host 10.12.194.2, no gateway), etc/udev/rules.d/86-roboscope-usb0-managed.rules
#        (NetworkManager ignores gadget interfaces unless told otherwise).
#        etc/systemd/system/roboscope-usb-watchdog.service runs
#        usr/local/lib/roboscope/usb-link-watchdog.sh, which reloads g_ether when the
#        host stops reaching the Pi (a safety net; the one-way link it was written for
#        is fixed by rule 87).
#      - etc/NetworkManager/conf.d/90-roboscope-wifi-powersave.conf: Wi-Fi power save
#        off (with it on, Wi-Fi pings averaged 1.6 s).
#      - Faster boot: etc/rpi/swap.conf.d/90-roboscope.conf (zram only, no swap file),
#        etc/cloud/cloud-init.disabled (cloud-init only matters on a fresh card).
#   3. Validates the sshd config before reloading sshd (a broken config is removed, so
#      SSH is never left unable to start).
#   4. Disables Bluetooth (service off now; dtoverlay=disable-bt from the next boot).
#   5. USB gadget: turns off the stock rpi-usb-gadget setup if it is on (its "shared"
#      mode enables IP forwarding + NAT), adds dtoverlay=dwc2,dr_mode=peripheral, and
#      enables roboscope-usb-dhcp.service and roboscope-usb-watchdog.service. The link
#      comes up from the next boot.
#   6. Moves the Wi-Fi connection out of netplan into a NetworkManager keyfile (see the
#      comment at that step: it cost ~55 s of every boot).
#   7. Turns on systemd "linger" for the login user ($SUDO_USER, normally max), so their
#      user manager keeps running between SSH sessions. Without it, the first login
#      after a gap started user@1000.service from scratch (3.1 s, >4 s CPU on the Zero),
#      and the camera tunnel's first request waited ~5-7 s for it (2026-11-26).
#   8. Installs the camera server code (staged roboscope_camera/ → /opt/roboscope) and
#      (re)starts roboscope-camera.service (unit in rootfs/etc/systemd/system).
# Reboot is left to the caller; the script prints whether one is needed.
# Run it over Wi-Fi, not over the USB link: step 5 can drop the USB link mid-run.
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

echo "== USB gadget: stock setup off"
# rpi-usb-gadget (preinstalled on Trixie) is replaced by our files. Its `off` removes its
# NetworkManager profiles, ICS watcher, modules-load file and the dwc2 overlay line; we
# add our own versions below. Run before installing rootfs so it can't remove ours.
if [ -f /etc/modules-load.d/usb-gadget.conf ] \
   || systemctl -q is-enabled rpi-usb-gadget-ics.service 2>/dev/null; then
    rpi-usb-gadget off >/dev/null
fi

echo "== config files"
# Files are listed relative to rootfs/ and installed at the same path under /.
(cd rootfs && find . -type f) | while read -r f; do
    mode=644
    case "$f" in */NetworkManager/system-connections/*) mode=600 ;; esac
    install -D -o root -g root -m "$mode" "rootfs/$f" "/${f#./}"
    echo "  /${f#./}"
done
systemctl daemon-reload

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

echo "== USB gadget: roboscope setup"
# g_ether is loaded by udev rule 87 once the USB controller exists. Remove the earlier
# modules-load.d file, which loaded it before the controller was ready.
rm -f /etc/modules-load.d/roboscope-usb-gadget.conf
# Peripheral mode for the Zero's USB data port (the one nearest the mini-HDMI).
if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral' "$CFG"; then
    printf '\n# roboscope: USB gadget link to the host (deployed by provision.sh)\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CFG"
    reboot_needed=1
fi
systemctl enable roboscope-usb-dhcp.service >/dev/null 2>&1
systemctl restart roboscope-usb-dhcp.service
systemctl enable roboscope-usb-watchdog.service >/dev/null 2>&1
systemctl restart roboscope-usb-watchdog.service
nmcli connection reload
# Apply the udev rule that lets NetworkManager manage usb0 (it ignores gadget
# interfaces by default), then bring usb0 up now if the gadget is already loaded
# (otherwise both happen at the next boot).
udevadm control --reload
if [ -e /sys/class/net/usb0 ]; then
    udevadm trigger --action=change /sys/class/net/usb0
    udevadm settle
    sleep 2                                 # NetworkManager reacts asynchronously
    nmcli -w 10 connection up roboscope-usb0 >/dev/null || echo "  usb0 not up yet" >&2
fi

echo "== Wi-Fi: power save off"
# Global conf.d settings are read on reload; the active connection picks them up the next
# time it (re)activates, at the latest at the next boot.
nmcli general reload conf

echo "== Wi-Fi: netplan -> NetworkManager keyfile"
# The Imager's Wi-Fi settings land in netplan (/etc/netplan/90-NM-*.yaml). On every boot
# NetworkManager rewrote those files, and each write ran `netplan generate` plus a
# systemd daemon-reload, which takes ~13 s on the Zero: 4 of them added ~55 s to boot
# (measured 2026-11-25). A plain keyfile in /etc/NetworkManager avoids that. The
# generated keyfile in /run already holds the full connection (including the Wi-Fi
# password, which stays on the Pi), so copy it, then move the netplan files aside to
# /root/netplan-backup. Same UUID, so the active connection carries on.
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
    echo "  netplan Wi-Fi config found but no generated keyfile; left alone" >&2
fi
shopt -u nullglob

echo "== linger"
loginctl enable-linger "${SUDO_USER:-max}"
loginctl show-user "${SUDO_USER:-max}" -p Linger

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
