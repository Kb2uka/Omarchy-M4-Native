# Known limitations: M4 Mac mini tester build

This tester build (v0.1.0-alpha.1-j773g-test.1) is for the plain M4 Mac mini,
Mac16,10 / J773g. Nobody has booted it on a Mac mini yet; it is not qualified
for any Mac. START-HERE.md has the install steps and what to report.

- **Display:** HDMI only, using the picture the Mac set up at power-on.
  Connect the monitor to HDMI before starting. USB-C and Thunderbolt monitors,
  more than one monitor and changing the resolution do not work.
- **Graphics:** drawn in software, without the GPU. Expect a slower desktop
  than on the Air, and no hardware video decoding.
- **USB:** only the two rear USB-C ports nearest the HDMI port are wired. The
  third rear port and both front ports are off.
- **Not in this build:** the speaker (it stays off until the mini has its own
  speaker profile), Widevine for protected movies, sleep, Thunderbolt and USB4
  devices, and M4 updates (`omarchy-m4-update` refuses this board).
- **Shutdown and restart** use the Air's tested poweroff path through the
  same power-management chip. They are untested on the mini.
- **Wi-Fi and Bluetooth** use the mini's firmware, collected by the installer
  from Apple's 26.6.2 recovery image. They are untested on the mini. Wired
  Ethernet is the recommended network for the first boot.

macOS stays installed. To go back, hold the power button at startup and
choose Macintosh HD.
