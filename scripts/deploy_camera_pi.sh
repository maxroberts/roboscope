#!/bin/bash
# Push the camera Pi's configuration from this repo and apply it.
#
# Copies hardware/camera_pi/{provision.sh,rootfs/} and the camera server code
# (src/roboscope/camera, staged as roboscope_camera/) to ~/roboscope-deploy on the Pi,
# then runs provision.sh there with sudo (see that script for what it changes). The
# repo is the source of truth for the Pi's setup: edit files here, then re-run this
# script. It needs SSH key access as $USER_ON_PI; sudo asks for the Pi password on the
# terminal (or reads it from stdin when not run from a terminal).
#
# Usage: scripts/deploy_camera_pi.sh [host]   (default roboscopeCam.local)
# Prefer the Pi's Wi-Fi IP as host when the USB link is plugged in: the .local name can
# resolve to the USB address, and provisioning can drop that link mid-run.
set -euo pipefail
HOST=${1:-roboscopeCam.local}
USER_ON_PI=${USER_ON_PI:-max}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/hardware/camera_pi"

rsync -a --delete -e "ssh -o BatchMode=yes" \
    "$SRC/provision.sh" "$SRC/rootfs" "$USER_ON_PI@$HOST:roboscope-deploy/"
rsync -a --delete --exclude __pycache__ -e "ssh -o BatchMode=yes" \
    "$ROOT/src/roboscope/camera/" "$USER_ON_PI@$HOST:roboscope-deploy/roboscope_camera/"
# -t gives sudo a terminal for the password prompt when run interactively.
if [ -t 0 ]; then
    ssh -t "$USER_ON_PI@$HOST" 'sudo bash roboscope-deploy/provision.sh'
else
    ssh -o BatchMode=yes "$USER_ON_PI@$HOST" 'sudo -S -p "" bash roboscope-deploy/provision.sh'
fi
