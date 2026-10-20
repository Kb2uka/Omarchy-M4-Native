# Known limitations: 15-inch M4 MacBook Air tester build

This tester build (v0.1.0-alpha.1-j715-test.1) is for the 15-inch M4 MacBook
Air, Mac16,13 / J715. One J715 has installed it; it is not qualified for any
Mac. START-HERE.md has the install steps and what to report.

- **Sleep:** do not suspend. On one J715 the panel stayed black after resume,
  and USB-C and Bluetooth needed rebinding. Closing the lid does not suspend.
- **Speakers and headphones:** off. The J715 has six speaker amplifiers and
  needs its own protected speaker profile, which this build does not carry;
  the 13-inch Air's speaker services refuse a J715 and are not started.
- **Bluetooth and USB audio:** off as well. The audio session manager
  (WirePlumber) waits for the 13-inch Air's speaker readiness check, which
  refuses a J715, so it does not start and its failures appear in the log.
- **Not in this build:** hardware video decoding, Widevine for protected
  movies, brightness control, the camera, CPU idle, the P-core boost and M4
  updates (`omarchy-m4-update` refuses this board).
- **CPU:** E cores reach 2808 MHz, protected by the 95 °C passive trip; P
  cores stay at 1800 MHz. For a moment at boot, before the thermal zone
  exists, P can reach 4464 MHz (issue #145).
- **GPU:** the module admits a J715 whose GPU power limit is at most
  24800 mW. One J715 reads 24552 mW.
- **Wi-Fi and Bluetooth** use the J715's `kos` firmware, collected by the
  installer from Apple's 26.5.1 recovery image.

macOS stays installed. To go back, hold the power button at startup and
choose Macintosh HD.
