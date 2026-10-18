# M4 Mac mini tester build (v0.1.0-alpha.1-j773g-test.1)

This build is for testers of the **plain M4 Mac mini (2024), Mac16,10 / J773g /
T8132** ([#136](https://github.com/Kb2uka/Omarchy-M4-Native/issues/136)). It
is not for the M4 Pro mini or any other Mac. It is not endorsed by Omarchy,
Asahi, Gravity or Apple.

**Nobody has booted it on a Mac mini yet.** It is the 13-inch Air's
installer update 4 with three changes for the mini:

- the Mac mini devicetree (`docs/j773g-boot-dtb.md`);
- the macOS 26.6.2 firmware the mini's installer profile uses;
- the desktop drawn in software on the HDMI screen, with the Air-only services
  turned off.

Installation changes partitions and boot policy. Back up your Mac first. macOS
stays installed and selectable at startup.

## What to expect

| Part | This build |
|---|---|
| Display | HDMI only, at the resolution the Mac picked at startup. Connect the monitor to the **HDMI port before you power on**. USB-C monitors do not work. |
| Graphics | Software drawing (no GPU acceleration yet), so it is slower than the Air |
| Keyboard and mouse | USB, in the **two rear USB-C ports nearest the HDMI port** (Gravity Linux names them back-left and back-middle; use a USB-C adapter or hub). The third rear port and the two front ports are not on yet, so if a keyboard does nothing, move it to another rear port. Bluetooth keyboards may work after setup. |
| Network | Wired Ethernet is expected to work; Wi-Fi may |
| Speaker, video decoding, protected movies, M4 updates | Off in this build |

## 1. Download (macOS)

In Terminal:

```sh
curl -fLO https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/v0.1.0-alpha.1-j773g-test.1/download-j713.command
bash download-j713.command
```

The downloader checks every file. It needs about 15 GB free, plus room for the
Apple recovery download and the Linux installation. The kit ends up in
`~/Downloads/Omarchy-M4-v0.1.0-alpha.1-j773g-test.1/installer/`.

## 2. Install (macOS)

Connect power, Ethernet and the HDMI monitor. Then, in Terminal:

```sh
cd "$HOME/Downloads/Omarchy-M4-v0.1.0-alpha.1-j773g-test.1/installer"
bash Install.command --install --tester
```

1. Wait for the checks to finish, then type `TEST` to confirm.
2. Enter your **macOS** password when asked. Nothing shows while you type.
3. The installer runs in expert mode. Choose the **Omarchy M4 Native ... J773g
   tester** entry.
4. If needed, let it shrink macOS to make room, and keep the recommended
   minimum. Choose **Install into free space**, answer `max` for the size, and
   name the OS **Omarchy M4**.
5. Follow the installer's on-screen recovery steps exactly. Apple requires you
   to approve the new OS in recovery with your macOS account. Do not cut power
   while it writes to disk.

## 3. First boot

Choose **Omarchy M4** at startup. You will see a console, then the setup
screen. Create your Linux username and password, and choose a timezone.

If the desktop does not appear, press **Control + Option + F2**. Sign in with
the account you just created, then collect the logs below.

## 4. Report back on #136

Whatever happens, a report helps. Please post:

- how far it got (photos of the screen are fine);
- if you can reach a login, the output of these commands:

```sh
sudo dmesg > ~/dmesg.txt
sudo journalctl -b --no-pager > ~/journal.txt
ip -br link; ip -br addr
cat /var/log/omarchy-m4-boot-status.json 2>/dev/null
```

Attach `dmesg.txt` and `journal.txt` to the issue.

## Going back to macOS

Shut down. Hold the power button until **Loading startup options** appears,
choose **Macintosh HD**, and optionally set it as the default startup disk.
To remove the Linux partitions later, ask on #136 first; the disk numbers on
your Mac matter.
