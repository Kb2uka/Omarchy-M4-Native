# J713 native Omarchy desktop checkpoint

Historical October 7 checkpoint. The [October 8 GPU and normal-root
checkpoint](j713-gpu-normal-root.md) records subsequent progress and its limits.

On October 7, 2026, the M4 MacBook Air J713/T8132 displayed the actual
Omarchy 4.0.4 interface in a native Linux RAM boot. The bar, wallpaper,
Foot terminal and searchable menu rendered through llvmpipe. The operator
confirmed terminal character entry and trackpad movement in the eighth image,
then opening search in the ninth image.
An unchanged repeat boot displayed the wallpaper after the terminal closed.

This is a graphical bring-up checkpoint, not a complete installed OS.
It does not qualify GPU acceleration, speakers, microphone, camera, suspend,
battery life, upgrades or sustained reliability. Software rendering uses the
CPU. Normal service/session integration remains under development.

## Inputs and boundaries

The kernel and device tree retain the merged J713 CPU, storage, radio and
[input work](j713-input-power-v2.md). Every launch reserves the live
[TrustCache range](j713-smp-trustcache.md). The existing m1n1 source and
allocation limits were not changed. Each boot's retained state capture
reported the internal SSD namespace as read-only, as listed below. The private
startup scripts mount pseudo-filesystems and RAM-backed paths; they do not mount
an internal filesystem.
When `/dev/nvme0n1` exists, the private RAM startup script runs
`blockdev --setro /dev/nvme0n1` and checks that `--getro` returns `1`.
The script uses `set -eu`, so a failed command or check stops startup.
If the device is absent at that point, the conditional check is skipped.
This blocks ordinary block-device writes to that namespace and its partitions;
it does not protect another namespace or prevent privileged NVMe passthrough.
The published NVMe driver patch does not supply this guard.

The kernel base is `77cb8f24c2381a8abb7272d7bbdec548d6426a8a`.
The [input checkpoint](j713-input-power-v2.md#captured-hardware-evidence)
records the matching complete experimental kernel diff and configuration
hashes, alongside this same Image.gz and r21 DTB. Those private bench inputs
include diagnostic changes; the public patches are not a complete image recipe.

| Input | Version or SHA-256 |
| --- | --- |
| Kernel | `7.1.9-m4checkpoint-20261006+`, input-v2 checkpoint |
| Kernel Image.gz | `e1a5bd4a73904e0b41c0082a30e49067cb97ffc26c53826398a0bfc57d99aa1c` |
| r21 DTB before per-boot reservation | `6a216236144b0bbebe1ee137bb542621318bd81472e9b54d7d639a9d4f84a15c` |
| Omarchy / settings | `4.0.4-1` / `4.0.4-5` |
| Hyprland / Aquamarine package / Quickshell | `0.56.2` / `0.15.1` (library replaced) / `0.3.1` |
| Aquamarine library in eighth and ninth images (local replacement build) | `80920ba08c464f06eb2d40f11ca8e9ba25bc40b904364fc5462484f4215c33d1` |
| Eighth image's `bin/start-desktop` | `ad2f41c5accab2bd7760c6d0c84e431b66ecd83d266e4ce2654ffc80807118ef` |
| Ninth image's `bin/start-desktop` | `2cb0924a7d507f856384b05f99757e94d181ffb0e8983fcafb9344fddba2b6b2` |
| Eighth RAM image, 492,390,892 bytes | `4e8499794c2e5402c40506c7ed59fd1545d514c76a80c722dc6549e53f87ebe7` |
| Ninth RAM image, 493,952,492 bytes | `809678383b80cf807843a1cbfcf2e55f4f5df973ada630bba7789474a6656f31` |

The library and startup-script hashes were checked against members extracted
from the pinned images. Both startup scripts contain the conditional storage
guard. The ninth image adds inotify-tools and a revised startup script that
creates `/dev/fd` and the standard-stream links at boot, allowing Omarchy's
packaged plugin scanner to run.

Arch Linux ARM and Omarchy packages were signature-checked on the build hosts.
The replacement library was built from Aquamarine `v0.15.1`, source archive
SHA-256 `2f9de98c0bd1b7b1b09c576e390a2fef436449762fb334163c414f0c300296f2`,
with the carried CRTC-rescan patch `375e80ec3826b54618fdd5f2db708c0f2bb936ae`
and the local GBM fallback. The carried patch file hashes to
`50e9e38ff915b18074dc9b5dd1d07d7f24e340e99f254476d6abe578eece7864`.
This local replacement is not covered by the package signature. The unpublished
`aquamarine-simpledrm-gbm.patch` is the generated source diff for the fallback;
it hashes to
`41ab83d3a66f35080efdeae9824c3f71af9eabf8621cf44f77bfbef7965ff879`.
The build applies those edits using `patch-aquamarine.py`, whose separate hash is
`7a36999847b6da5a2bf00ef6735639f8eff01070c3e2465568f54f859daabe38`.
Some package hooks failed in the cross-architecture chroot; root ownership
was corrected and an ephemeral machine ID was created natively. This was
not a clean, fully validated installed-system transaction.
The RAM image itself is a private bench artifact: it contains board firmware
and ephemeral SSH configuration and is not published as a user image.
The kernel command line retains `maxcpus=10 idle=nop panic=0` and
`dockchannel_hid.m4_power_v2=1`. Recovery is armed for 120 seconds at Linux
handoff, after image upload.

## Userland findings

The initial chroot lacked runtime mounts normally supplied by OS startup.
Missing `/dev/shm` caused Wayland format-table and keymap descriptor errors.
Mounting it as tmpfs with mode 1777 and binding it into the desktop root
allowed windows to render. Subsequent images start with this setup already
present, alongside devpts and its ptmx link.

Omarchy's shell then loaded but discovered zero plugins. Its packaged
`PluginRegistry.qml` scanner uses Bash process substitution, which needs
`/dev/fd`. Running that exact scanner on the build host reproduced the failure:

```text
before: /dev/fd/63: No such file or directory
before: zero plugin manifests
with /dev/fd -> /proc/self/fd: 37 manifests, including menu and bar
```

The ninth image includes signed inotify-tools and a revised startup script that
creates `/dev/fd` and the standard stream links at boot. Omarchy's packaged shell
discovers its plugins and renders the bar and interactive menu.
The checkpoint does not replace Omarchy's shell
with a lookalike interface.

The image also carries an experimental Aquamarine GBM fallback, guarded by
`AQ_M4_SIMPLEDRM_GBM=1`, a missing render node and an exact `simpledrm` driver
match. Guard checks passed on a build host. An isolated comparison of the
original library with the corrected shared-memory setup is still outstanding;
the fallback is not claimed to be the sole cause of desktop success.
Hyprland's packaged software-renderer work includes the
[M3-tested upstream change](https://github.com/hyprwm/Hyprland/pull/16343).
M3 results are useful precedent, not proof of M4 GPU acceleration.

A 703 MiB initial Omarchy image exceeded the loader's existing 1 GiB allocation
arena, which also holds a 512 MiB kernel buffer and other allocations. This
failed before Linux handoff. Stronger compression and removal of unused
build resources and stock Hyprland wallpapers produced a base that fits.
Omarchy backgrounds were retained. The actual loader allocator was exercised
on a build host to reproduce the large-image failure and verify the smaller
image's fit; the native boots then verified successful loading.

## Native evidence

The first successful full-interface boot began at `20261007T201637Z`.
The identical photo repeat began at `20261007T202025Z`.
The following is an annotated summary of captured output and observations,
not a verbatim single-command log.

```text
DESKTOP_PTY_READY
DESKTOP_SHM_READY
DESKTOP_PROCESS_SUBSTITUTION_READY
shell IPC ping: ok
plugin entries: 37
mapped layers: omarchy-background, omarchy-bar, omarchy-menu
fresh menu screenshot: search text "dis", matching results displayed
operator, eighth image (20261007T200823Z): terminal echoed; trackpad works
operator, ninth image: search opened
repeat screenshot: bar and wallpaper after terminal closed
SMP_MEMORY_PASS
read_only=1
READPROBE_PASS direct 8388608 bytes twice at three offsets; hashes equal
watchdog proxy recovery: 126.446 / 126.390 seconds
```

Final repeat dmesg contains no BUG, Oops, WARNING, Call trace or kernel panic.
The measured interval includes recovery and re-enumeration, so it exceeds the
120-second hardware timeout. No long-term stability claim follows from this.

The retained state captures report `read_only=1` and `READPROBE_PASS` separately
for all three boots. These are observations during each bounded run, not a
guarantee that the conditional startup guard handles a device appearing later.

| Boot | State capture SHA-256 |
| --- | --- |
| Eighth, `20261007T200823Z` | `004252ad0a207bc44c636e81ff308422bd08da769cf27b57edb2a6a6f7aeb58d` |
| Ninth, `20261007T201637Z` | `462cfd015d1171a64282dc75e43e5fe79e4e05dffb6a161e3b82491eeda1ff1a` |
| Photo repeat, `20261007T202025Z` | `756c158433c201197039c56ed33f5afbebebadebccc637071689245ffe6de10e` |

Retained capture hashes:

| Capture | SHA-256 |
| --- | --- |
| Fresh interactive menu PNG | `75c29e30548b72d34c472ceff92f46c91d5cf6b7e2312e966686332191277cd5` |
| Wallpaper repeat PNG | `5c25dbb4a60e8e895f9194ab62ec44c3d533c6efdb5caaabf5a4ad3619012a74` |

The captures show the debug-launch warning because this checkpoint starts
Hyprland directly. BusyBox remains PID 1 in the ninth image, with manually
started system and session buses. Standard application launching through
UWSM and a fully managed login session were not validated in this checkpoint.
Later service-manager experiments must be qualified separately.

## Installation and update work

A production image still needs a complete package recipe, an installed-root
and boot strategy, recovery tests and qualification of hardware-dependent
services. Directly replacing a shared library in a RAM image is an experiment,
not a maintainable package update strategy.

A proposed maintenance model is to reuse upstream Omarchy packages, package
M4-specific changes separately, and test a coherent package set before
promoting an M4 stable channel. Users could then use the normal
`omarchy update` flow. The M4 kernel and boot object now ship that way through
the signed `[omarchy-m4]` channel ([package-channel.md](package-channel.md)).
Graphics, decoder and audio payloads are not packaged yet. No claim is made
that arbitrary upstream updates are safe for this experimental image.
