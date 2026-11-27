#!/bin/bash
# Open the camera live view in a browser, from any machine with an SSH key on the host Pi.
# Needs only ssh (not the repo or uv), so it suits a second computer. It prints the URL
# and does not open a browser on this machine (often one reached only over remote
# desktop), so browse to the printed URL from wherever you are.
#
# The camera hub (roboscope-camera.service on the host Pi, 3B+) listens only on the
# 3B+'s loopback, port 47623. It gets frames from the camera Zero over the USB link and
# renders the preview itself. This script opens one SSH local port forward:
#     this machine 0.0.0.0:47623 -> 3B+ (Wi-Fi) -> hub on 127.0.0.1:47623
# so nothing touches the Zero's Wi-Fi. It waits until the forward is up, prints
# http://127.0.0.1:47623/ (and the LAN URL when shared), and keeps the tunnel open until
# Ctrl-C (or until SSH drops).
#
# By default the forward listens on all interfaces, so other computers on the LAN can
# open http://<this machine's IP>:47623/ (printed at start) with no SSH key or script.
# While it runs, ANYONE on the LAN can view the camera and change its settings: the page
# has no login. It can't move the mount, and closing the script closes it. The user chose
# this default (2026-11-26). `--private` listens on loopback only. macOS may ask once
# whether to allow incoming connections for ssh. With the repo, `uv run python
# scripts/camera_view.py` opens a private (loopback) tunnel the same way.
#
# Usage: scripts/camera_view_via_host.sh [--private] [host_pi] [local_port]
#   --private   listen on this machine's loopback only (no LAN access)
#   host_pi     the 3B+ (default roboscope.local)
#   local_port  default 47623 (= roboscope.camera.client.PORT); change it if another
#               camera view already holds that port
# Needs: this machine's SSH key authorized on the 3B+, and the 3B+'s host key known here
# (run `ssh max@<host_pi> true` once and accept it).
set -euo pipefail

BIND=0.0.0.0                         # open to the LAN by default; see above
if [ "${1:-}" = "--private" ]; then BIND=127.0.0.1; shift; fi
HOST_PI=${1:-roboscope.local}
LPORT=${2:-47623}
REMOTE_PORT=47623                    # roboscope.camera.client.PORT
USER_ON_PI=${USER_ON_PI:-max}
URL="http://127.0.0.1:$LPORT/"

if nc -z 127.0.0.1 "$LPORT" 2>/dev/null; then
    echo "port $LPORT is already in use on this machine (another camera view?)." >&2
    echo "Close it, or pass another port: $0 $HOST_PI 47624" >&2
    exit 1
fi

echo "tunnel: this machine:$LPORT -> $HOST_PI -> camera hub"
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=8 \
    -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -L "$BIND:$LPORT:127.0.0.1:$REMOTE_PORT" "$USER_ON_PI@$HOST_PI" &
SSH_PID=$!
# Close the tunnel however the script ends (Ctrl-C, kill, or error).
trap 'kill "$SSH_PID" 2>/dev/null || true' EXIT INT TERM

# Wait up to 20 s for the forward to accept connections.
for _ in $(seq 1 40); do
    if ! kill -0 "$SSH_PID" 2>/dev/null; then
        echo "ssh exited; check that $HOST_PI is up and its host key is known" >&2
        echo "(ssh max@$HOST_PI true), and that roboscope-camera runs there." >&2
        exit 1
    fi
    nc -z 127.0.0.1 "$LPORT" 2>/dev/null && break
    sleep 0.5
done
nc -z 127.0.0.1 "$LPORT" 2>/dev/null || { echo "tunnel did not come up within 20 s" >&2; exit 1; }

echo "camera view: $URL  (Ctrl-C to close)"
if [ "$BIND" = 0.0.0.0 ]; then
    # This machine's LAN address: the interface of the default route.
    ifc=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
    ip=$( (ipconfig getifaddr "$ifc" || hostname -I | cut -d" " -f1) 2>/dev/null)
    echo "SHARED on the LAN: http://${ip:-THIS-MACHINE-IP}:$LPORT/  (no login; anyone on the LAN can change camera settings)"
fi
wait "$SSH_PID"
