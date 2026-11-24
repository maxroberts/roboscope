"""roboscope: self-calibrating, camera-guided control for a Meade StarNavigator NG telescope.

Details:
    Status: `mount` exists and is tested against a simulator only, not yet against real
    hardware. All other modules are planned.

    The telescope is a Meade StarNavigator NG (single-arm alt-az mount) driven by an
    AudioStar handbox. The computer talks to the handbox over RS-232 at 9600 8N1 using
    the Meade LX200/Autostar command set (see documentation/TelescopeProtocol_2010-10.pdf).
    The handbox, not this package, owns the motors and its own pointing model; this
    package sends it commands and corrects its errors using camera feedback.

    Modules:
      - mount:    (exists) LX200 formatting/parsing, serial transport, the typed `Mount`
                  API, and a simulated handbox. Written fresh from the protocol doc; the
                  earlier telescope.py was used only for its keypad codes.
    Planned:
      - motion:   backlash-aware moves, cable-wrap limits, slip detection.
      - sky:      ephemerides and star catalog (time + location -> alt/az of targets).
      - camera:   frame capture from the imaging camera.
      - solve:    star detection and plate solving (image -> where the scope points).
      - align:    pointing model fitted from solved frames; spiral search; tracking.

    Intended deployment: a Raspberry Pi next to the telescope runs this package, with
    the handbox and camera on its USB ports; development and analysis happen over SSH.
"""
