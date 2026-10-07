# Linux and macOS proxy host

This procedure covers the host launcher, not an Omarchy installation. It
requires the separately preserved m1n1 checkout, kernel, initramfs and DTBs.
Keep known-control binaries unchanged. Hardware enablement remains experimental.

The historical runbooks use a Mac-specific launcher and a USB reappearance
observer. The current launcher replaces those commands. A USB transition is
transport evidence, not proof that Linux reached userspace or initialized a device.

## Host preparation

Use Bash, Python with the dependencies from your m1n1 checkout, and `script`
(util-linux on Linux, BSD on macOS). Choose an interpreter whose environment
contains those dependencies. Do not assume system Python has them.

Set the directory containing these external assets:

```text
boot-kit/
  m1n1/proxyclient/tools/linux.py
  Image-m4hacks-smc-nvme.gz
  initramfs.cpio.gz
  dtb-night3/r1-kernel-control.dtb
```

The target must already be configured for m1n1 proxy mode. Follow the current
[m1n1 user guide](https://asahilinux.org/docs/sw/m1n1-user-guide/) for owner-authenticated
Recovery setup. The launcher does not change boot security, configure the
bootloader, power-cycle the machine, mount a root filesystem, or install a system.
Inspect the selected kernel and initramfs separately: their behavior is not
constrained by the host launcher.

When m1n1 enumerates, identify its **proxy** interface, distinct from its
hypervisor UART interface. Linux generally exposes `/dev/ttyACM*`; macOS exposes
`/dev/cu.usbmodem*`. The interface must be readable and writable by your user.
Never select a different serial device merely because the expected one is absent.

## One boot

Example for Linux; substitute paths and the actual target device:

```bash
export BOOT_KIT=/absolute/path/to/boot-kit
export PYTHON=/absolute/path/to/venv/bin/python
export M1N1DEVICE=/dev/ttyACM0
bash tools/boot.sh "$BOOT_KIT/dtb-night3/r1-kernel-control.dtb" control
```

Set `PYTHON` explicitly on either host to the interpreter with the m1n1
dependencies, as in the Linux example. On both Linux and macOS, set
`M1N1DEVICE` to the Air's actual primary proxy path; `/dev/ttyACM0` is only
an example and can belong to a different device. The default interpreter is PATH-resolved `python3`, not
the historical `/usr/bin/python3`. The launcher chooses the
appropriate `script` invocation automatically. File paths may contain spaces,
quotes and shell metacharacters. Tags may contain letters, numbers, `_` and `-`.

The optional third argument selects a kernel without overwriting the control:

```bash
bash tools/boot.sh "$BOOT_KIT/dtb-night3/r1-kernel-control.dtb" fallback /absolute/path/to/fallback.gz
```

`BOOT_ARGS` overrides the complete argument string. The default is:

```text
nosmp idle=nop panic=0 console=tty0 fbcon=nodefer loglevel=7
```

`panic=0` leaves a panicked kernel halted for inspection instead of automatically
rebooting. The other arguments retain the historical minimal boot configuration;
they are not production power-management settings. Hold the arguments, kernel
and initramfs fixed when comparing different DTBs.

Logs use unique names under `$BOOT_KIT/logs/`. They record SHA-256 hashes,
arguments, selected device, host OS, loader output and loader exit status. A
serial exception after handoff is possible; preserve its actual status rather
than replacing it with a success code. No observer is launched in the background.

## Hardware evidence

Start with the minimal control, then enable storage alone. Continue with SMC,
MTP input and USB only as the preceding evidence allows. The historical
[night-3 ladder](night3-runbook.md) supplies the staged DTB names, but its
automatic-reboot expectations no longer apply. Its preserved storage kernel
predates the [queue-size correction](nvme-queue-size.md). Keep it as a historical
control; explicitly select the corrected candidate as the third argument.
The default kernel filename still names the preserved historical control.
First boot any new candidate with the minimal control DTB:

```bash
bash tools/boot.sh "$BOOT_KIT/dtb-night3/r1-kernel-control.dtb" corrected-control "$BOOT_KIT/Image-m4hacks-smc-nvme-ioq-20261006.gz"
```

Record and verify the control result before starting a separate storage run:

```bash
bash tools/boot.sh "$BOOT_KIT/dtb-night3/r2-storage-only.dtb" corrected-storage "$BOOT_KIT/Image-m4hacks-smc-nvme-ioq-20261006.gz"
```

For each run, record the artifact hashes, argument string, timestamp, final panel
photo and observed probe lines. A loader exit of zero is not a Linux boot verdict.
For storage, require `nvme0n1` in the target's output; for input, require a typed
command and its response. Do not mount or write the internal disk during these
bring-up checks. Graphics and audio require their own later hardware evidence.

## Launcher regression checks

Run the regression check below:

```bash
python3 tests/test_boot.py
```

The checks use a harmless fake loader and `/dev/null`, never m1n1 or a physical
serial connection. They exercise real PTY recording, paths, arguments, missing
inputs and child failure status on Linux and macOS. They prove host-tool behavior
only. No Windows-host implementation is supplied.
