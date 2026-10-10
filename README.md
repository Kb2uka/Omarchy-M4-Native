![Omarchy M4 Native concept artwork](docs/images/omarchy-m4-native.png)

# Omarchy on the M4 MacBook Air — bring-up research

Experimental Linux device-tree patches, kernel changes, and research from an
attempt to run **Omarchy natively on a 13-inch M4 MacBook Air (2025)**.
Maintained by **KB2UKA** and shared so other Apple Silicon and Omarchy
enthusiasts can inspect, reproduce, and build on the findings.

**The milestone, October 9, 2026:** Omarchy 4.0.4 has reached its desktop from
an installed ext4 root on the Air's internal SSD. The owner confirmed Wi-Fi,
terminal and browser operation; EGL reports the Apple M4 renderer. Package
and startup omissions were repaired after installation. See the
[installed desktop checkpoint](docs/j713-desktop-image.md) and the earlier
[GPU and RAM-root checkpoint](docs/j713-gpu-normal-root.md).
The owner confirmed one normal repeat boot with a working trackpad. Installed
boot required verbose mode; reliable cold boot, updates, camera, suspend and daily-use reliability remain
unqualified. This repository contains research and selected source helpers,
not a supported installer or disk image.

**Audio checkpoint, October 10, 2026:** The owner confirmed internal speaker
playback, and the exact pinned J713 runtime completed automatic registration and
protection startup on a normal reboot. See the [audio source and qualification
guide](docs/j713-audio.md). This records the tested installation; fresh-image
deployment, broader reboot coverage, audio suspend/resume, safe updates and a
generally distributable runtime remain unqualified or blocked as documented.

## Project status

The September work reached a framebuffer console and was paused. Hardware
testing resumed on October 6. October 7 work demonstrated ten-CPU memory
checks, read-only SSD reads, Wi-Fi internet access, Bluetooth HCI startup,
physical input and the software-rendered Omarchy interface. The detailed
hardware notes below record the tested revisions and limitations. October 8
adds GPU rendering, normal systemd startup with an automatic UWSM session,
and RAM root identity checks. October 9 reached an installed SSD desktop, with
the remaining limitations described above.

The October 8 RAM desktop tests used a fresh per-boot TrustCache reservation, `idle=nop`, a
read-only internal SSD and a two-minute watchdog recovery window. No internal
filesystem was mounted or written during these tests; filesystem writes and
remount checks used RAM-backed disks only. The later owner-operated installation
created a separate SSD root. Reproducible installation,
updates, long-running sessions and complete hardware support are still work
in progress.

All results below describe **this project's recorded revisions and test
machine**, not the current support status of Asahi Linux or every M4 Mac.
Check the [Asahi M4 feature page](https://asahilinux.org/docs/platform/feature-support/m4/)
for upstream progress.

October 9 adds an [experimental J713 installer profile](docs/j713-installer-profile.md)
with exact recovery-image and radio-firmware checks. The resulting experimental
installation reached the SSD desktop but still needs complete hardware and boot qualification.
The [SSD root preparation](docs/j713-ssd-root-preparation.md) adds selected-partition
identity, locally assembled boot firmware, an early root with checked ARM
libraries, and corrected offline ARM owner provisioning.

## Hardware and approach

| Item | Research target |
| --- | --- |
| Machine | MacBook Air, 13-inch, M4, 2025 |
| Model / board | `Mac16,12` / `J713` (board ID `0x2c`) |
| SoC | Apple `T8132`, M4 |
| Boot paths tested | m1n1 USB proxy to a RAM ext4 root; installed m1n1/kernel/initramfs payload to an SSD ext4 root |
| Display during bring-up | simpledrm scanout with experimental Apple M4 GPU rendering |
| Proxy host | A separate Linux or macOS machine, with USB access to the target |
| Kernel build host | Separate Linux machine with an AArch64 LLVM toolchain |

The aim was bare metal. Virtual machines were outside the final project
scope. The initial roadmap is preserved in [SPEC.md](SPEC.md), dated to
avoid confusing the original ambitions with completed functionality.

## What we did

### 1. Mapped the hardware and extended the device trees

We compared the target's macOS IODeviceTree and live Apple Device Tree (ADT)
with the T8132 baseline and existing Asahi driver interfaces. Other Apple
SoCs supplied binding and driver precedents; they were not treated as proof
that the M4 used identical addresses, interrupts, or wiring.

Ten initial patch slices described storage/SMC, SPI, USB/ATC, SEP, SPMI/PMU,
Type-C, PCIe, MTP input transport, SIO, and AOP. Later patches addressed
findings from the first hardware session and the offline follow-up.

The detailed [gap analysis](docs/t8132-gap-analysis.md) records which values
came from the target, which came from a precedent, and which still need a
hardware test. A device-tree node existing or compiling does **not** establish
that its peripheral works.

### 2. Reached Linux on the actual Air in the initial bring-up

The recorded kernel was an Asahi-derived `7.1.9+` build. Reaching the console
required `idle=nop`, disabling the WFxT delay path, and bypassing particular
AIC EL2 register accesses in the experimental kernel. These are bring-up
hacks, not production-quality fixes: the patch disables paths broadly and
must not be treated as safe for other Apple machines or normal kernel use.

We also removed depopulated `atc2`/`atc3` power-manager domains on J713 after
they blocked progress. With the ANS/SART/NVMe group disabled, Linux printed
the board identity and reached a BusyBox `/ #` prompt on the internal panel.
The built-in keyboard was not working in that configuration, so the prompt
is evidence of reaching userspace, not of a usable interactive laptop.

### 3. Isolated the storage failure and prepared a candidate fix

Enabling the storage group produced a silent early boot wedge; disabling it
allowed the shell prompt. The offline investigation then found that the M4
uses separate NVMe controller and NVMMU register windows:

| Resource | ADT tuple | Linux physical address |
| --- | --- | --- |
| NVMe controller | ANS `reg[9]` | `0x4c5cc0000` |
| NVMMU | ANS `reg[3]` | `0x485cc0000` |

The earlier description pointed the controller at the NVMMU window. Patch
0013 separates them and uses `apple,t8132-nvme-ans2` without a generic fallback
that could bind an incompatible driver. Its companion kernel patch maps the
separate resource and adds the IO queue base writes at `0x1200` and `0x1208`,
following the M4 sequence studied in m1n1. The October 6 correction also
programs the zero-based IO queue size at `0x1210` after both queue base writes,
following [the upstream v2 patch](https://lists.openwall.net/linux-kernel/2026/09/06/915).
See [queue-size evidence and reproduction](docs/nvme-queue-size.md).

That split-window correction alone did not resolve the October 6 storage
stall. Subsequent [ANS receive IRQ](docs/j713-ans-receive-irq.md) and
[NVMe power-domain](docs/j713-nvme-power-domain.md) work reached `nvme0n1` and
repeated direct reads. The latest checkpoint reads 8 MiB twice at each of
three offsets with matching hashes and the device marked read-only. Internal SSD filesystem
mounting, writes and an installed root filesystem have not been tested.
RAM-backed ext4 mounting and write/remount checks are recorded separately in
the October 8 checkpoint.

### 4. Audited the descriptions against the live ADT

The standalone Python tools compare MMIO ranges, IRQs, IOMMU stream IDs, and
power-manager relationships. The [recorded audit](docs/t8132-adt-audit.md)
reports 48 address-correlated `MATCH` rows, one intentional
`MATCH-OVERSIZE`, and two `DT-ONLY` rows after patch 0013. That summary applies
to the MMIO comparison; the report also documents unresolved PMGR differences
and other gaps. It is not an all-hardware-passed result.

The ADT also established USB stream IDs `1` and `14` and identified the
J713 HPM/USB-PD topology as SPMI rather than the initially suspected I2C path.
These findings improved the descriptions but did not establish working USB
or charging under Linux.

## What worked, and what did not

| Area | Evidence through October 8 |
| --- | --- |
| m1n1 USB proxy and recovery | Native RAM boots; watchdog return and fresh proxy verified |
| CPU / SMP | All ten CPUs pass bounded memory checks; `idle=nop` still required |
| NVMe / internal SSD | Repeated direct reads with matching hashes; read-only, no filesystem mount/write |
| USB diagnostic link | USB Ethernet link-local transport and key-only SSH demonstrated |
| Keyboard and trackpad | [Physical events and haptics confirmed](docs/j713-input-power-v2.md#captured-hardware-evidence); [graphical typing and pointer movement confirmed in the eighth RAM image](docs/j713-omarchy-desktop.md#native-evidence) |
| Wi-Fi | [Scan, association, DHCP, DNS and internet request demonstrated in radio test](docs/j713-radio-bringup.md) |
| Bluetooth | HCI startup demonstrated; pairing and audio unqualified |
| Omarchy desktop | Interface demonstrated in RAM; systemd/UWSM startup demonstrated October 8 |
| Graphics acceleration | Apple M4 renderer demonstrated in bounded RAM tests; compatibility and sustained use unqualified |
| Root filesystem | Exact RAM GPT partition selected by identity; root write passed; remount persistence passed on a separate RAM ext4 disk; wrong ID rejected |
| Audio, camera, suspend, battery life | Unqualified |
| SMC, charging, thermal management; general USB ports | Unqualified for daily use; diagnostic USB transport does not establish general peripheral support |
| Installed OS and updates | No installed Omarchy root filesystem or qualified update path |

The Air was restored to normal macOS operation after the September work.
October testing uses a USB proxy boot session and bounded recovery. These
results do not establish production safety or all-hardware support.
When `/dev/nvme0n1` is present, the private RAM startup script sets it read-only
and checks the flag; otherwise that check is skipped. The published NVMe driver
patch does not supply that guard. The startup guard covers ordinary
block-device writes to that namespace, not privileged NVMe passthrough or other
namespaces. Experimental boots can still affect the machine or its data.

## Repository map

| Path | Contents |
| --- | --- |
| [patches/](patches/) | Ordered Linux device-tree patch series |
| [patches-kernel/](patches-kernel/) | Boot hacks and the proposed NVMe/NVMMU driver/binding change |
| [docs/t8132-gap-analysis.md](docs/t8132-gap-analysis.md) | Detailed derivations, session findings, and unresolved questions |
| [docs/t8132-adt-audit.md](docs/t8132-adt-audit.md) | Recorded offline audit and power-manager analysis |
| [docs/first-boot-runbook.md](docs/first-boot-runbook.md) | Historical first-session notes, including failed approaches and corrections |
| [docs/night3-runbook.md](docs/night3-runbook.md) | Historical incremental validation plan; use the current host and kernel notes below |
| [docs/j713-omarchy-desktop.md](docs/j713-omarchy-desktop.md) | Historical October 7 software-rendered desktop checkpoint |
| [docs/j713-gpu-normal-root.md](docs/j713-gpu-normal-root.md) | October 8 GPU, normal-root and error-path evidence |
| [tools/m4-root-identity.sh](tools/m4-root-identity.sh) | Experimental read-only root identity resolver; does not mount or install |
| [docs/host-bench.md](docs/host-bench.md) | Current Linux/macOS host commands and evidence gates |
| [docs/nvme-queue-size.md](docs/nvme-queue-size.md) | Current kernel queue-size correction and regression command |
| [tools/adt.py](tools/adt.py) | Standalone binary ADT reader using Python's standard library |
| [tools/adt-audit.py](tools/adt-audit.py) | ADT versus compiled DTB comparison |
| [tools/mkdtb.sh](tools/mkdtb.sh), [tools/boot.sh](tools/boot.sh) | DTB builder and Linux/macOS proxy launcher; require external boot assets |
| [tests/](tests/) | Offline regression checks for host tools and driver changes |
| [recon/](recon/) | Derived PMGR description and capture availability notes |

The raw machine captures are withheld from the public tree because they
contain machine-specific identifiers. The original research and Git/PR history are preserved
privately. Public history starts from a clean research snapshot; old commit
identifiers in the notes refer to the historical work, not commits available
in this public repository. Published tables and derivations remain available; see
[recon/README.md](recon/README.md) for the resulting reproduction limits.

## Inspecting or reproducing the research

Start with the gap analysis, then the audit, then the night-3 plan. Read
later corrections before following any older command. The historical notes
contain machine-specific paths and observations; they are not a universal
installation guide.

```sh
git clone https://github.com/Kb2uka/Omarchy-M4-Native.git
cd Omarchy-M4-Native
```

For offline analysis, use your own locally captured ADT. Python 3.10 or later
is a conservative baseline for the bundled tools; no third-party Python
packages are required by these two analysis scripts.

```sh
python3 tools/adt.py /path/to/your-adt.bin /arm-io/ans
python3 tools/adt-audit.py --help
```

A DTB comparison additionally needs a compiled device tree and the matching
PMGR source. See the command and input descriptions in the audit document.
Do not upload an unreviewed capture: ADTs and IORegistry dumps can expose
serial numbers and other identifiers.

### Version and artifact limits

The notes record a device-tree checkout at `32be170d0`, later carrying the
local slices and follow-up commits, and a separate kernel build at
`77cb8f24c`. The repository does not vendor either tree. Those abbreviated
historical identifiers are provenance clues, not a promise that a fresh
upstream clone can resolve them or that the patch series applies to today's
branch. Confirm the exact base and patch applicability before building.

The series mixes plain unified diffs with mail-formatted patches. Use
`git apply --check` to assess each patch on the appropriate staged source;
do not assume the whole set can be consumed by `git am`.

Apply the numbered DT patches in order only against a compatible source
baseline. Do not assume an already-patched historical checkout needs the
whole series again. Do not apply the kernel patches blindly to an
arbitrary kernel or use them on a production system.

Also absent are the built kernels, initramfs and its complete build recipe,
m1n1 sources/binaries, per-rung DTBs, full boot logs, photographs, and the
historical `reboot-probe.sh` helper. The current launcher does not require that
helper. Therefore **a clone is
not a complete reproducible boot kit**.

Both shell helpers default to the original `~/omarchy-firstboot` layout.
`tools/mkdtb.sh` builds a DTB with clang and dtc and adds `/opt/homebrew/bin`
to its search path; adapt the source and tool paths for your host. It does
not select a proxy device.

The boot wrapper `tools/boot.sh` supports Linux (util-linux `script`) and
macOS (BSD `script`). Set `M1N1DEVICE` to the target's actual proxy interface;
set `BOOT_KIT` and `PYTHON` when the boot assets or interpreter live elsewhere.
It preserves the loader exit status and records artifact hashes and a PTY
transcript. It does not infer Linux boot success from USB disappearance or
reappearance. Automatic panic reboot is disabled by default (`panic=0`).
See the [host bench procedure](docs/host-bench.md), which supersedes the
historical launch commands. Windows proxy hosts are not supported by this helper.

For a direct call to m1n1's `tools/linux.py`, the
[historical first-boot runbook](docs/first-boot-runbook.md) shows how the Mac
session exported `M1N1DEVICE`. Use the device actually enumerated on your
proxy host, not the placeholder in these notes: macOS commonly uses
`/dev/cu.usbmodem*`, while Linux commonly uses `/dev/ttyACM*`. These notes do
not provide a Windows proxy setup.

Recovery/security changes and experimental internal-storage drivers warrant
a backed-up test machine and a recovery plan. Consult current Asahi and
Apple documentation before changing boot security. Nothing here should be
run as an unattended installer.

## Useful next work

1. Preserve a reproducible package/build recipe for the demonstrated RAM desktop.
2. Prepare owner provisioning and qualify application launching beyond the bounded desktop test.
3. Qualify graphics compatibility, audio, camera, power management, suspend and sustained operation.
4. Design and test installation and recovery on an explicitly allocated target.
5. Package board-specific changes and validate coherent upstream updates before
   publishing an M4 update channel. Such a channel does not exist yet.

For useful issue reports, include the board/SoC, exact source revisions,
patch list, kernel configuration, boot arguments, artifact hashes, and
redacted logs. Distinguish an offline derivation, a successful compile, and
an observed hardware result. Reports from another board are valuable but do
not establish J713 equivalence.

## Credits and related projects

Project work: **KB2UKA**. This research depends on the groundwork of the
[Asahi Linux contributors](https://asahilinux.org/),
[Linux kernel contributors](https://www.kernel.org/), and
[m1n1](https://github.com/AsahiLinux/m1n1). Their existing driver and
device-tree notices remain applicable. The historical boot investigation
also references Yureka's M4 work.

The desktop is [Omarchy](https://github.com/basecamp/omarchy).
[omarchy-mac](https://github.com/omarchy-mac/omarchy-mac) is related work at
the distribution/integration layer. This repository is an independent
experiment, not an official release or endorsement by those projects.
Upstream projects have their own contribution requirements; publication here
does not establish that these experimental patches are ready for submission.

## Licensing

This is a mixed-license repository. See [LICENSE.md](LICENSE.md) for the
file-by-file scope and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for
upstream attribution. Full license texts are in [LICENSES/](LICENSES/).
Original standalone tools and prose are MIT-licensed; DT work preserves
`GPL-2.0-or-later OR MIT`; Linux code changes retain their source-file GPL
terms, and the NVMe binding retains its dual license. No license here grants
rights to Apple firmware, macOS, trademarks, or separately obtained binaries.
