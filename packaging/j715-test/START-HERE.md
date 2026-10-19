# 15-inch M4 MacBook Air tester build (v0.1.0-alpha.1-j715-test.1)

This build is for testers of the **15-inch M4 MacBook Air (2025), Mac16,13 /
J715 / T8132** ([#142](https://github.com/Kb2uka/Omarchy-M4-Native/issues/142)).
It is not for the 13-inch Air or any other Mac. It is not endorsed by Omarchy,
Asahi, Gravity or Apple.

**This kit has not been installed on a J715 yet.** One J715 has run its
kernel, devicetree and GPU module from an earlier unofficial kit. It is the
13-inch Air's installer update 4 with these changes for the 15-inch Air:

- the J715 devicetree (`docs/j715-boot-dtb.md`) and a kernel that also accepts
  the J715's built-in keyboard and trackpad;
- the installer's `j715ap` profile, which collects the J715's Wi-Fi and
  Bluetooth firmware from the same macOS 26.5.1 recovery image as the 13-inch Air;
- a GPU module that admits the J715, and the 13-inch Air's speaker, video and
  update services turned off.

Installation changes partitions and boot policy. Back up your Mac first. macOS
stays installed and selectable at startup.

## What to expect

| Part | This build |
|---|---|
| Display and graphics | The built-in panel, with GPU acceleration |
| Keyboard and trackpad | Built-in. Brightness keys do nothing |
| USB | Both USB-C ports |
| Network | Wi-Fi and Bluetooth |
| Power | Shut down and restart work; **do not use Suspend** (the panel stays black after resume) |
| Speakers, headphones, video decoding, protected movies, M4 updates | Off in this build |

## 1. Download (macOS)

In Terminal:

```sh
curl -fLO https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/v0.1.0-alpha.1-j715-test.1/download-j713.command
bash download-j713.command
```

The downloader checks every file. It needs about 15 GB free, plus room for the
Apple recovery download and the Linux installation. The kit ends up in
`~/Downloads/Omarchy-M4-v0.1.0-alpha.1-j715-test.1/installer/`.

## 2. Install (macOS)

Connect power. Then, in Terminal:

```sh
cd "$HOME/Downloads/Omarchy-M4-v0.1.0-alpha.1-j715-test.1/installer"
bash Install.command --install --tester
```

1. Wait for the checks to finish, then type `TEST` to confirm.
2. Enter your **macOS** password when asked. Nothing shows while you type.
3. The installer runs in expert mode. Choose the **Omarchy M4 Native ... J715
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

## 4. Report back on #142

Whatever happens, a report helps. Please post:

- how far it got (photos of the screen are fine);
- if you can reach a login, the output of these commands:

```sh
sudo dmesg > ~/dmesg.txt
sudo journalctl -b --no-pager > ~/journal.txt
cat /var/log/omarchy-m4-boot-status.json 2>/dev/null
```

Attach `dmesg.txt` and `journal.txt` to the issue.

## Going back to macOS

Shut down. Hold the power button until **Loading startup options** appears,
choose **Macintosh HD**, and optionally set it as the default startup disk.
To remove the Linux partitions later, ask on #142 first; the disk numbers on
your Mac matter.
