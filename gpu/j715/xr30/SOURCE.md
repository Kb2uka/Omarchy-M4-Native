# XR30 Mesa source

The source candidate starts from GravityLinux/mesa commit
`87ac51d411f0e58a9433567ec2666f457be89e1d`
(`asahi/apple9: export legacy clip vertex as an ordinary varying`).

The GitHub commit archive has SHA-256:

```text
1195f212bcf5816b8b893113b0b5dc578ce744d420856b6ad4f42008889e231e
```

The package applies `mesa-xr30.patch` to that source. The patch adds the two
packed X2 formats to the Apple9 color and block-export helpers and includes
their compiler regression tests. It does not import the separate ongoing
compiler, shared-buffer, or Vulkan experiments.

The build enables Gallium Asahi, EGL, GBM, GLVND, X11 and Wayland. Vulkan and
video acceleration are disabled. The package keeps its libraries under
`/usr/lib/omarchy-m4/mesa`; it does not replace the distribution Mesa libraries
or the existing `/opt/m4-mesa` installation.

The October 24 frozen compositor build is separate evidence. It included
additional source changes and Vulkan, so its binary hash does not identify
this source candidate. This package requires its own build and hardware
qualification.

Retain the Mesa source's upstream copyright and license notices.
