# Raspberry Pi techniques used in roboscope

A practical reference for the Pi tricks used while bringing up the host Pi (3B+) and the
camera Pi (Zero W), November 2026. Each section covers what the technique is for, the
commands, and what we observed on our hardware. Experiment details and numbers are in
`hardware_findings.md`.
Commands that change things need `sudo`. Pi names: **3B+** = `roboscope` (host),
**Zero** = `roboscopeCam` (camera).

## 1. Cutting power to USB devices (`uhubctl`)

`uhubctl` [1] switches the power to a USB hub's ports on and off from software. The
devices really lose power, as if unplugged, so it gives a true **cold power-up**. A
plain reboot of the device is not the same thing.

On the 3B+, all four USB ports are ganged: the uhubctl README says "Main hub `1-1`, all 4
ports ganged, all controlled by port `2` (turns off secondary hub ports as well)" [1].

```sh
sudo uhubctl                         # list hubs, ports and what's attached
sudo uhubctl -l 1-1 -p 2 -a off      # power off ALL USB ports on a 3B+
sudo uhubctl -l 1-1 -p 2 -a on       # and back on
```

Observed on our 3B+ (2026-11-26):
- **Port-level switching does nothing:** `uhubctl -l 1-1.1 -p 3 -a off` reported the
  port off, but the Zero on it kept running (same boot ID).
- **The ganged switch works:** `-l 1-1 -p 2 -a off` for 8 s cold-booted the Zero
  every time (46 cycles). It also cuts every other USB device, including the Uno.
- **Wi-Fi stayed up:** the uhubctl README says the secondary hub's port 1 "is used for
  Ethernet+WiFi" [1]. But on ours, SSH over Wi-Fi kept working while the ports were
  off, which fits Wi-Fi being on SDIO, not USB. The 3B+'s Ethernet is on USB and does
  drop.

What it's good for:
- Automated cold-boot testing, as the cold-cycle experiments in `hardware_findings.md`
  did.
- Recovering a wedged USB device remotely, without anyone at the telescope.

**Safety:** the Uno takes its power from the Pi's USB. Cutting it leaves the mount's
motor bus floating if the mount is powered, and a floating bus once made the mount move
on its own (`.claude/CLAUDE.md`). So only use the ganged switch with the mount off, or
with the Uno on its own power.

## 2. Resetting a USB device without cutting its power

- **From the host:** de-authorize and re-authorize the device, which makes it
  disconnect and enumerate again:
  ```sh
  echo 0 | sudo tee /sys/bus/usb/devices/1-1.1.3/authorized
  echo 1 | sudo tee /sys/bus/usb/devices/1-1.1.3/authorized
  ```
  The path (`1-1.1.3`) is the bus-port location, shown by `lsusb -t` or in `dmesg`.
  When the Zero's gadget was wedged this failed (`can't set config #1, error -110`),
  which showed the stuck end was the device's, not the host's.
- **From the device (USB gadget side):** reload the gadget driver. The Zero then
  disconnects and reconnects as if replugged:
  ```sh
  sudo modprobe -r g_ether && sudo modprobe g_ether
  ```
  This fixed every wedged link we saw, and `roboscope-usb-watchdog` does it
  automatically.

## 3. Checking the power supply

**The firmware's view:** `vcgencmd get_throttled` returns a bit pattern [2].

| Bit | Value | Meaning [2] |
|---|---|---|
| 0 | `0x1` | Undervoltage detected |
| 1 | `0x2` | Arm frequency capped |
| 2 | `0x4` | Currently throttled |
| 3 | `0x8` | Soft temperature limit active |
| 16 | `0x10000` | Undervoltage has occurred |
| 17 | `0x20000` | Arm frequency capping has occurred |
| 18 | `0x40000` | Throttling has occurred |
| 19 | `0x80000` | Soft temperature limit has occurred |

So `0x0` is healthy. `0x50005` means low voltage *now* and throttled now, and both have
happened since boot. `0x50000` means it happened earlier but not at the moment.

The threshold: "low-voltage detection circuitry that will detect if the supply voltage
drops below 4.63 V (±5%). This will result in an entry being added to the kernel log"
[3]. The same page says this applies to every model since the B+ "except the Zero
range" [3], so the camera Zero can't report low voltage itself.

```sh
vcgencmd get_throttled                                    # flags, as above
journalctl -k | grep -E "Undervoltage|Voltage normalised" # each event, with times
vcgencmd measure_clock arm      # CPU clock in Hz: 600 MHz under load = being held back
vcgencmd measure_temp           # rules out heat as the reason for a slowdown
```

Things we learned the hard way:
- **`measure_volts` doesn't measure the supply.** It reports the CPU core voltage
  (~1.2 V), not the 5 V input. The 3B+ can't measure its input voltage, only whether
  it is below the threshold.
- **The flag can't rank supplies.** Under load it was set 89–99% of the time with every
  supply we tried, while a multimeter read a healthy ~4.9 V average on the best one.
  - Our guess: brief dips, with the bit held between them. The Raspberry Pi docs give
    no hold time [2], so this is unconfirmed.
  - To compare supplies, use a meter, not the flag.
- **Measuring the real voltage:** put a multimeter on GPIO header **pin 2** (5 V; the
  corner pin of the outer row, at the end away from the USB ports) against any metal
  port shell as ground.
  - Don't bridge pins 4 and 6 (5 V next to ground).
  - Don't touch a 3.3 V signal pin with the 5 V probe.
  - A meter's MIN/MAX mode can catch slower dips.
- **Load tests:** use one busy loop per core: `for c in 1 2 3 4; do timeout 60 sh -c
  'while :; do :; done' & done`.
  - A loop like that understates real draw. Provisioning (Wi-Fi, SD writes, the CPU at
    full speed) drew more than 1 A on the bench supply, more than the loops did,
    because they had pushed the CPU down to 600 MHz.

## 4. USB gadget networking (the Zero as a USB Ethernet device)

A Pi Zero's data port can act as a USB device. With `g_ether` loaded, the host sees a
USB Ethernet adapter, so one cable gives both power and a fast, private network link:
14 MB/s, about 0.5 ms round trip. Our setup is in `hardware/camera_pi`. The key points:
- **Peripheral mode:** `dtoverlay=dwc2,dr_mode=peripheral` in `/boot/firmware/config.txt`.
- **Fixed MACs:** `g_ether` options `dev_addr` / `host_addr` in `/etc/modprobe.d/`.
  Without them the MACs change every boot, and the host sees a "new" adapter each time.
- **NetworkManager ignores gadget interfaces by default:** a stock udev rule
  (`85-nm-unmanaged.rules`) marks them unmanaged. Our rule 86 overrides it for `usb0`.
- **Open `usb0` immediately:** after a cold power-up the link often came up one-way
  when `usb0` opened more than a few seconds after the host enumerated the gadget
  (19 of 32 failed; 0 of 23 with it opened quickly). Our rule 87 loads `g_ether` and
  opens `usb0` in one command.
- **Use a static address on a fast-booting host:** the 3B+ boots faster than the Zero,
  and its DHCP request timed out, so it now uses a static 10.12.194.2.

## 5. udev rules as "when this device appears, do X"

A udev rule can run a command the moment the kernel reports a device, which beats
guessing a boot order. Ours:
- `87-roboscope-gadget-load.rules`: when USB controller `20980000.usb` appears, load
  `g_ether` and open `usb0`.
- `88-roboscope-usb0-up-early.rules`: when `usb0` appears, open it.

To see what a rule can match on: `udevadm info /sys/class/udc/20980000.usb` (or any
`/sys` path). After editing rules: `sudo udevadm control --reload`.

## 6. Boot and reboot diagnostics

```sh
systemd-analyze                  # total boot time (kernel + userspace)
systemd-analyze blame | head     # slowest units
cat /proc/sys/kernel/random/boot_id   # changes every boot: reliable reboot detection
journalctl --list-boots          # needs a persistent journal (below)
journalctl -b -1 -k              # kernel log of the previous boot
journalctl -b -o short-monotonic # seconds since boot, unaffected by clock jumps
```

- **Persistent journal:** Pi OS keeps the journal in RAM only
  (`40-rpi-volatile-storage.conf`), so everything is lost at reboot, including the
  clues to an unclean reset. The 3B+ now keeps it on the card, capped at 100 MB
  (`hardware/host_pi`, journald drop-in).
- **Clocks before network time:** a Pi has no real-time clock. Until the network time
  sync, its clock can be minutes off and then jump. That cut a timed test short once
  (only 2 samples). Use `-o short-monotonic`, or a machine with a synced clock, for
  timing.
- **Boot-time trims we made:**
  - Moving the Wi-Fi config from netplan to a NetworkManager keyfile, so
    NetworkManager stops regenerating it every boot.
  - A zram-only swap.
  - Disabling cloud-init after first boot.
  - Result: Zero 3:04 → 1:15, and 3B+ 1:05 → 0:18.

## 7. Network and SSH scripting

- **systemd linger, for faster SSH logins:** without it, a user's systemd manager
  (`user@1000.service`) stops after their last session ends and starts again at the
  next login. On the Zero that made the first login after a gap take ~5 s instead of
  ~1 s. `sudo loginctl enable-linger max` keeps it running;
  `loginctl show-user max -p Linger` checks it. The camera Pi has it on.

- **Temporary NetworkManager changes:** `nmcli connection modify --temporary …` changes
  a profile in memory only. `nmcli connection reload` restores it from disk. We used
  this to fake a broken host without touching repo-deployed files.
- **Interface counters:** `ip -s link show usb0` shows packets, errors and drops per
  direction. "rx stuck at 1, tx rising" was the signature of the one-way link.
- **SSH that survives reboots:** use `ssh -o BatchMode=yes -o ConnectTimeout=4 -o
  ServerAliveInterval=2 -o ServerAliveCountMax=2`. Without keepalives, a connection
  made just as a Pi shuts down can hang forever.
- **sudo in scripts:** `sudo -S -p ""` reads the password from stdin, so it never goes
  on a command line or into a file.

## 8. SD card checks

- **Full check:** `f3write` and then `f3read` on the mounted card [4] (from Homebrew on
  the Mac). It finds fake capacity and bad sectors. Our 32 GB card took about 2 h and
  passed, but writes slowly (4.6 MB/s).
- **Quick speed check:** write and read back a temp file with the Mac's cache bypassed
  (`fcntl F_NOCACHE`). Short tests flatter writes, because cards have fast buffers.
- **Persistence check:** write a marker file, `sync`, cut the power, and check it after
  boot. A card going quietly read-only accepts writes and then loses them.

## References

1. uhubctl README: https://github.com/mvp/uhubctl. The
   Raspberry Pi 3B+ section and the FAQ were quoted from it on 2026-11-26.
2. Raspberry Pi documentation, "vcgencmd → get_throttled" (Raspberry Pi OS page):
   https://www.raspberrypi.com/documentation/computers/os.html. The page's source,
   from which the bit table was checked verbatim on 2026-11-26:
   https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/os/graphics-utilities.adoc
3. Raspberry Pi documentation, "Power supply warnings" (Raspberry Pi hardware page):
   https://www.raspberrypi.com/documentation/computers/raspberry-pi.html. The source,
   with the 4.63 V sentence checked verbatim on 2026-11-26:
   https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/power-supplies.adoc
4. f3 (Fight Flash Fraud), project homepage: https://fight-flash-fraud.readthedocs.io/
   *(the tool was used directly; this page was not re-read for this document)*.
