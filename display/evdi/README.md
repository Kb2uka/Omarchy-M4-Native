# evdi for the J713 kernel (USB external displays, #58)

DisplayLink's [evdi](https://github.com/DisplayLink/evdi) 1.15.1 is a virtual
DRM display: the compositor draws into it like any monitor, and a user-space
client reads the frames out. DisplayLink USB adapters and docks work that way.
Synaptics' DisplayLinkManager is the client and sends the frames over USB.
`omarchy-m4-evdi` ships evdi's two GPL/LGPL parts for the J713 kernel; it does
not ship DisplayLinkManager. See
[J713 external displays](../../docs/j713-external-displays.md) for what works
and how to set it up.

The shipped builds are `evdi.ko`
`c5e65a2bcba6433ff8369fea449ccc4e6be178a392b9c4a959884415953c671a` (182,816
bytes) and `libevdi.so.1.15.1`
`0f1da52db1f7cbd7e0c420d47285ddee78e22dc0a57e789305c1456b163034ce` (73,264
bytes), pinned in `packaging/omarchy-m4/channel.json` and staged by the
`omarchy-m4-evdi` package. Both come from the upstream `v1.15.1` tag archive,
`evdi-1.15.1.tar.gz`
`b120df303d14be09062157aab17a8598746c0caf2e076ff3388b4091186cf2d9`, unchanged.

## Build

`build.sh` takes the pinned inputs and refuses any others.

The module is built on the alpha.1 kernel tree (`kernel-controller.tar.gz`,
SHA-256 `810339d8…`) in `m4-gravity-builder:20261008` (clang 21.1.8), with the
job directory at `/job`, the layout of `cpu/j713/build-module.sh`:

```sh
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/job:/job" -v "$PWD:/w" \
  m4-gravity-builder:20261008 bash /w/display/evdi/build.sh module \
  /w/kernel-controller.tar.gz /w/evdi-1.15.1.tar.gz /w/out
```

It prints the `vermagic` (`7.1.9-m4-gpu-backport-20261008 SMP preempt
mod_unload aarch64`), the dependency `drm_ttm_helper` and the SHA-256. The
tree's `Module.symvers` lists only `vmlinux` exports, so the script names the
two `drm_ttm_helper.ko` exports evdi calls; without them modpost refuses the
module, and with them `modprobe evdi` loads that kernel module first.

The library is built for aarch64 in `m4-gravity-mesa-arm-builder:20261008`
(`aarch64-linux-gnu-gcc` 15.2 and the arm64 libdrm headers):

```sh
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/job:/job" -v "$PWD:/w" \
  m4-gravity-mesa-arm-builder:20261008 bash /w/display/evdi/build.sh library \
  /w/evdi-1.15.1.tar.gz /w/out
```

It needs only `libc.so.6` (symbols up to `GLIBC_2.33`); its soname is
`libevdi.so.1`. The package installs it as `/usr/lib/libevdi.so`, the same file
Arch's `evdi` package installs, and `ldconfig` adds the soname link.

Both builds were run twice in separate job directories on 2026-10-17 and gave
the same bytes each time.

## Bench tool

`bench/evdi_probe.c` plugs a virtual 1920x1080 monitor into evdi and reads back
what the compositor draws, the job DisplayLinkManager does for a real adapter.
It is not packaged. Build it against the library and run it as root with evdi
loaded:

```sh
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -I evdi-1.15.1/library \
  -o evdi_probe display/evdi/bench/evdi_probe.c libevdi.so.1.15.1
sudo LD_LIBRARY_PATH=. ./evdi_probe 20 frame.ppm
```

It prints the mode the compositor set and, for each second, the frames it
grabbed and how many differed from the one before; then the totals and the
share of non-black pixels in the last frame, which it writes as a PPM image. It
asks for the next frame as soon as it has one, as DisplayLinkManager does, so a
compositor drawing at 60 Hz shows 60 changed frames a second.
