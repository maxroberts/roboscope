"""roboscope: self-calibrating, camera-guided control for a Meade StarNavigator NG telescope.

Details:
    Status: `mount` runs the real mount through our own Arduino motor controller
    (handbox no longer needed). `camera` runs the real Pi camera. `sky`, `align` and
    `calibration` exist and are tested; alignment is so far simulation-only, because
    the hardware adapters (motion) don't exist yet.

    The telescope is a Meade StarNavigator NG (single-arm alt-az mount). Its AudioStar
    handbox has been replaced: an Arduino Uno (firmware/mount_controller) is master of
    the base's motor bus, and this package commands it over USB serial. All pointing
    and tracking logic lives here. Camera feedback will provide absolute pointing.

    Modules:
      - mount:    (exists) `MotorController` for our own Uno motor controller (main
                  path: speeds, encoder positions, limits); plus the older LX200/handbox
                  driver (`Mount`) and simulators for both.
      - sky:      (exists) site, Skyfield/DE421 positions with refraction, alt/az <->
                  J2000, Sun-safety check, bright-star catalog with alignment-star
                  selection for wide or narrow cameras.
      - align:    (exists) pointing model fitted from observations (any camera), camera
                  frame geometry, spiral search, star-alignment session; simulation-tested.
      - calibration: (exists) the instrument's measured properties (counts/°,
                  backlash, pointing hardware terms, camera frames) from
                  config/hardware_calibration.json, with uncertainty and provenance.
      - camera:   (exists) server on the camera Pi (raw frames, star centroid, browser
                  live view), host client over an SSH tunnel whose `locate()` is
                  align's star finder; numpy-only star finding shared by both.
    Planned:
      - motion:   backlash-aware moves, hardware adapters for align, cable-wrap limits,
                  slip detection, tracking.
      - solve:    multi-star detection and plate solving (image -> where the scope
                  points), for the wide camera.

    Intended deployment: a Raspberry Pi next to the telescope runs this package with
    the motor controller on USB. The camera Pi runs only roboscope.camera's server.
    Development and analysis happen over SSH.
"""
