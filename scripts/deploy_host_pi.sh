#!/bin/bash
# Push the host Pi's (Pi 3B+) configuration and the roboscope code from this repo.
#
# Three steps, run from the Mac at the repo root:
#   1. config: copies hardware/host_pi/{provision.sh,rootfs/} to ~/roboscope-deploy on
#      the 3B+ and runs provision.sh there with sudo (see that script for what it
#      changes). sudo asks for the Pi password on the terminal, or reads it from stdin
#      when not run from a terminal.
#   2. camera link: authorizes the 3B+'s SSH key (made by provision.sh) on the camera
#      Zero, and records the Zero's host key in the 3B+'s known_hosts for 10.12.194.1
#      (the USB link) and roboscopeCam.local. The Zero's key is read from the Zero over
#      the Mac's own trusted SSH connection, so the 3B+ never trusts it blindly.
#   3. code: rsyncs the repo (without .venv, .git and caches; with data/, the sky
#      catalog and ephemeris the tests need) to ~/roboscope on the 3B+, runs
#      `uv sync --dev` there, then restarts the camera hub (a user service, so no sudo). Files deleted from the repo are deleted on the Pi too,
#      except under data/, where the Pi may keep files of its own (logs, frames).
# The repo is the source of truth: edit files here, then re-run this script.
#
# Usage: scripts/deploy_host_pi.sh [--code-only] [host] [camera_host]
#   host        the 3B+ (default roboscope.local)
#   camera_host the Zero as the Mac reaches it (default roboscopeCam.local)
#   --code-only skip steps 1 and 2 (no sudo needed)
set -euo pipefail
CODE_ONLY=0; [ "${1:-}" = "--code-only" ] && { CODE_ONLY=1; shift; }
HOST=${1:-roboscope.local}
CAM=${2:-roboscopeCam.local}
USER_ON_PI=${USER_ON_PI:-max}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -o BatchMode=yes)

if [ $CODE_ONLY -eq 0 ]; then
    echo "### 1. config"
    rsync -a --delete -e "ssh -o BatchMode=yes" \
        "$ROOT/hardware/host_pi/provision.sh" "$ROOT/hardware/host_pi/rootfs" \
        "$USER_ON_PI@$HOST:roboscope-deploy/"
    # -t gives sudo a terminal for the password prompt when run interactively.
    if [ -t 0 ]; then
        ssh -t "$USER_ON_PI@$HOST" 'sudo bash roboscope-deploy/provision.sh'
    else
        "${SSH[@]}" "$USER_ON_PI@$HOST" 'sudo -S -p "" bash roboscope-deploy/provision.sh'
    fi

    echo "### 2. camera link"
    pub=$("${SSH[@]}" "$USER_ON_PI@$HOST" 'cat ~/.ssh/id_ed25519.pub')
    # Append to the Zero's authorized_keys only if that exact key isn't there yet.
    printf '%s\n' "$pub" | "${SSH[@]}" "$USER_ON_PI@$CAM" \
        'mkdir -p -m 700 ~/.ssh; read -r k; grep -qxF "$k" ~/.ssh/authorized_keys 2>/dev/null \
         || { echo "$k" >> ~/.ssh/authorized_keys; echo "  key added to the Zero"; }'
    hostkey=$("${SSH[@]}" "$USER_ON_PI@$CAM" 'cut -d" " -f1,2 /etc/ssh/ssh_host_ed25519_key.pub')
    printf '%s\n' "10.12.194.1,roboscopeCam.local $hostkey" | "${SSH[@]}" "$USER_ON_PI@$HOST" \
        'read -r line; touch ~/.ssh/known_hosts; grep -qxF "$line" ~/.ssh/known_hosts \
         || { echo "$line" >> ~/.ssh/known_hosts; echo "  Zero host key recorded"; }'
fi

echo "### 3. code"
rsync -a --delete --exclude .venv --exclude .git --exclude __pycache__ \
    --exclude .pytest_cache --exclude .ipynb_checkpoints --exclude .DS_Store \
    --filter 'P data/***' \
    -e "ssh -o BatchMode=yes" "$ROOT/" "$USER_ON_PI@$HOST:roboscope/"
"${SSH[@]}" "$USER_ON_PI@$HOST" 'cd roboscope && ~/.local/bin/uv sync --dev \
    && systemctl --user daemon-reload \
    && if systemctl --user -q is-enabled roboscope-camera.service; then
           systemctl --user restart roboscope-camera.service && echo "camera hub restarted"
       else echo "camera hub not enabled (run the full deploy first)"; fi'
echo "deploy done"
