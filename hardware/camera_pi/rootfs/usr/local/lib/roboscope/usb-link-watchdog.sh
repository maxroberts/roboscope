#!/bin/bash
# roboscope camera Pi: USB link watchdog. Recovers the USB gadget link when it comes up
# one-way (the host's frames stop reaching the Zero).
# Deployed from the roboscope repo (hardware/camera_pi/rootfs); edit it there, not on the Pi.
# Run by roboscope-usb-watchdog.service as root; logs go to its journal.
#
# The failure (documentation/hardware_findings.md, 2026-11-25 and 2026-11-26): after a
# cold power-up the host keeps receiving from the Zero, but its sends time out, and a
# host-side USB reset can't clear it because the Zero's gadget no longer answers.
# Reloading g_ether on the Zero fixes it. The cause (usb0 opened too late after the host
# enumerated the gadget) is fixed by udev rule 87-roboscope-gadget-load: 0 of 12 cold
# power-ups failed since. This watchdog is kept as a safety net.
#
# Detection: every CHECK_S seconds, while a host has configured the gadget (UDC state
# "configured") and usb0 exists, ping the host end (10.12.194.2) over usb0. A reply has
# to travel host -> Zero, the direction that fails. After FAILS_TO_ACT consecutive
# misses, reload g_ether, then wait before checking again. If the link still fails
# after a reload (for example a host that never configures its end), the wait doubles
# each time, up to MAX_WAIT_S, so a host that never answers doesn't cause a reload loop.
# Any successful ping resets the count and the wait.
HOST_IP=10.12.194.2
CHECK_S=10
FAILS_TO_ACT=3
MIN_WAIT_S=60
MAX_WAIT_S=1800

fails=0
wait_s=$MIN_WAIT_S
while sleep "$CHECK_S"; do
    state=$(cat /sys/class/udc/*/state 2>/dev/null)
    if [ "$state" != configured ] || [ ! -e /sys/class/net/usb0 ]; then
        fails=0                                  # no host attached: nothing to check
        continue
    fi
    if ping -c 1 -W 2 -I usb0 "$HOST_IP" >/dev/null 2>&1; then
        [ "$fails" -gt 0 ] && echo "link ok again after $fails missed checks"
        fails=0
        wait_s=$MIN_WAIT_S
        continue
    fi
    fails=$((fails + 1))
    [ "$fails" -lt "$FAILS_TO_ACT" ] && continue
    rx=$(cat /sys/class/net/usb0/statistics/rx_packets)
    tx=$(cat /sys/class/net/usb0/statistics/tx_packets)
    echo "no reply from $HOST_IP over usb0 in $fails checks (usb0 rx=$rx tx=$tx); reloading g_ether"
    modprobe -r g_ether
    sleep 1
    modprobe g_ether
    echo "g_ether reloaded; next check in ${wait_s}s"
    fails=0
    sleep "$wait_s"
    wait_s=$((wait_s * 2 > MAX_WAIT_S ? MAX_WAIT_S : wait_s * 2))
done
