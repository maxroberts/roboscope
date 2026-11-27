#!/bin/bash
# Compile firmware/mount_controller on this machine and upload it to the Uno on the host Pi.
#
# The Uno is plugged into the 3B+ (/dev/ttyACM0), so arduino-cli compiles the sketch
# here, the .hex goes to the 3B+ over scp, and avrdude (installed there by
# hardware/host_pi/provision.sh) writes it. The camera hub holds the Uno's port while it
# runs (its --mount), so the script stops the hub first and starts it again afterwards,
# whatever happens.
#
# Safe with the mount powered: the 47 kΩ pull-ups keep the motor bus idle while the Uno
# resets. Like any port opening, the upload resets the Uno: positions re-zero and the
# default limits return (the hub lifts both soft limits again when it reconnects). The
# motor boards may be out of bus sync for ~5 s afterwards until auto-recovery. Encoder
# calibration survives (it lives in the motor boards until the mount is powered off).
#
# Usage: scripts/upload_firmware_host_pi.sh [host_pi]   (default roboscope.local)
# Needs: arduino-cli with the arduino:avr core here; SSH key access to the 3B+.
set -euo pipefail

HOST_PI=${1:-roboscope.local}
USER_ON_PI=${USER_ON_PI:-max}
PORT=/dev/ttyACM0
REPO=$(cd "$(dirname "$0")/.." && pwd)
BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT

echo "== compile"
arduino-cli compile --fqbn arduino:avr:uno --output-dir "$BUILD" "$REPO/firmware/mount_controller" | tail -2
scp -q "$BUILD/mount_controller.ino.hex" "$USER_ON_PI@$HOST_PI:/tmp/mount_controller.hex"

echo "== upload on $HOST_PI (hub stopped meanwhile)"
ssh "$USER_ON_PI@$HOST_PI" "
    systemctl --user stop roboscope-camera
    trap 'systemctl --user start roboscope-camera; echo \"camera hub started\"' EXIT
    avrdude -q -p atmega328p -c arduino -P $PORT -b 115200 -D -U flash:w:/tmp/mount_controller.hex:i 2>&1 | grep -vE '^\s*$' | tail -4
    rm -f /tmp/mount_controller.hex
"
echo "upload done"
